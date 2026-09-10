"""Administrative CLI. Import exit codes: 0 success/dry-run, 2 rejected, 1 failure."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import secrets
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from runnerx.auth import hash_api_key
from runnerx.config import get_settings
from runnerx.db import get_engine
from runnerx.etl import import_file
from runnerx.models import ApiKey, Bootcamp, Tenant
from runnerx.sample import generate_sample


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _get_tenant(db, slug):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise ValueError("Tenant not found. Run create-tenant first.")
    return tenant


def _upsert_tenant(db, slug, name, token):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
        raise ValueError("Tenant slug must be 1..64 lowercase letters, digits or hyphens")
    if not name.strip() or len(name) > 120:
        raise ValueError("Tenant name must contain 1..120 characters")
    if len(token) < 32:
        raise ValueError("API keys must have at least 32 characters")
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        tenant = Tenant(slug=slug, name=name)
        db.add(tenant)
        db.flush()
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)))
    if key and (key.tenant_id != tenant.id or not key.is_active):
        raise ValueError("This API key is already associated with another tenant or has been revoked")
    if key is None:
        db.add(ApiKey(tenant_id=tenant.id, name="CLI provisioned", key_hash=hash_api_key(token)))
        db.flush()
    return tenant


def _seed_demo(db, data_dir):
    if get_settings().app_env == "production":
        raise ValueError("seed-demo is disabled in production")
    token = os.getenv("DEMO_API_KEY", "")
    if not token:
        raise ValueError("Set DEMO_API_KEY to a local demo token of at least 32 characters")
    files = {kind: data_dir / filename for kind, filename in (
        ("registrations", "registrations.xlsx"), ("sessions", "sessions.csv"))}
    if not all(path.is_file() for path in files.values()):
        raise ValueError("Sample files not found; run generate-sample first")
    tenant = _upsert_tenant(db, "demo", "Synthetic Demo Running Club", token)
    camp = db.scalar(select(Bootcamp).where(Bootcamp.tenant_id == tenant.id, Bootcamp.code == "demo-2026"))
    if camp is None:
        camp = Bootcamp(tenant_id=tenant.id, code="demo-2026", name="Synthetic Four Week Camp",
                       city="Demo City", start_date=date(2026, 1, 5), end_date=date(2026, 2, 1))
        db.add(camp)
        db.flush()
    results = []
    for kind, path in files.items():
        result = import_file(db, tenant_id=tenant.id, bootcamp_id=camp.id, path=path, kind=kind)
        if result["status"] == "rejected":
            raise ValueError(f"Sample {kind} was rejected; run import --dry-run for diagnostics")
        results.append(result)
    return {"tenant_id": tenant.id, "tenant_slug": tenant.slug, "bootcamp_id": camp.id,
            "bootcamp_code": camp.code, "imports": results}


def parser():
    result = argparse.ArgumentParser(description="RunnerX database and data pipeline tools")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Apply versioned Alembic migrations from this checkout")
    sample = commands.add_parser("generate-sample", help="Generate deterministic, entirely synthetic fixtures")
    sample.add_argument("--output", type=Path, default=Path("data/sample"))
    sample.add_argument("--runners", type=int, default=30)
    sample.add_argument("--seed", type=int, default=42)
    seed = commands.add_parser("seed-demo", help="Create demo tenant/camp and import existing sample files")
    seed.add_argument("--data-dir", type=Path, default=Path("data/sample"))
    tenant = commands.add_parser("create-tenant", help="Provision a tenant and print a generated API key once")
    tenant.add_argument("--slug", required=True)
    tenant.add_argument("--name", required=True)
    tenant.add_argument("--api-key-env", help="Read the key from this environment variable instead of generating one")
    revoke = commands.add_parser("revoke-key", help="Revoke a key by its database UUID")
    revoke.add_argument("--tenant", required=True)
    revoke.add_argument("--key-id", required=True)
    keys = commands.add_parser("list-keys", help="List key IDs and status without exposing token values or hashes")
    keys.add_argument("--tenant", required=True)
    importer = commands.add_parser("import", help="Validate/import one complete file for an existing tenant and camp")
    importer.add_argument("--tenant", required=True, help="Tenant slug")
    importer.add_argument("--bootcamp", required=True, help="Bootcamp code")
    importer.add_argument("--kind", choices=["registrations", "sessions"], required=True)
    importer.add_argument("--file", type=Path, required=True)
    importer.add_argument("--dry-run", action="store_true")
    importer.add_argument("--report", type=Path, help="Also write the JSON validation report to this path")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        get_settings()
        if args.command == "generate-sample":
            _emit(generate_sample(args.output, args.runners, args.seed))
            return 0
        if args.command == "init-db":
            config = Path.cwd() / "alembic.ini"
            if not config.is_file():
                raise ValueError("Run init-db from the repository root containing alembic.ini")
            command.upgrade(Config(str(config)), "head")
            _emit({"status": "schema up to date"})
            return 0
        with Session(get_engine(), expire_on_commit=False) as db:
            if args.command == "seed-demo":
                value = _seed_demo(db, args.data_dir)
            elif args.command == "create-tenant":
                token = os.getenv(args.api_key_env, "") if args.api_key_env else secrets.token_urlsafe(32)
                tenant = _upsert_tenant(db, args.slug, args.name, token)
                value = {"tenant_id": tenant.id, "slug": tenant.slug}
                if not args.api_key_env:
                    value["api_key"] = token
                    value["note"] = "Store this key securely; only its digest is retained"
            elif args.command in ("revoke-key", "list-keys"):
                tenant = _get_tenant(db, args.tenant)
                if args.command == "list-keys":
                    value = [{"id": key.id, "name": key.name, "is_active": key.is_active}
                             for key in db.scalars(select(ApiKey).where(ApiKey.tenant_id == tenant.id))]
                else:
                    key = db.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant.id, ApiKey.id == args.key_id))
                    if key is None:
                        raise ValueError("API key not found in this tenant")
                    key.is_active = False
                    value = {"key_id": key.id, "status": "revoked"}
            else:
                tenant = _get_tenant(db, args.tenant)
                camp = db.scalar(select(Bootcamp).where(Bootcamp.tenant_id == tenant.id, Bootcamp.code == args.bootcamp))
                if camp is None:
                    raise ValueError("Bootcamp not found in this tenant")
                value = import_file(db, tenant_id=tenant.id, bootcamp_id=camp.id, path=args.file,
                                    kind=args.kind, dry_run=args.dry_run)
            if args.command == "import" and args.dry_run:
                db.rollback()
            else:
                db.commit()
        if args.command == "import" and args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        _emit(value)
        return 2 if isinstance(value, dict) and value.get("status") == "rejected" else 0
    except SQLAlchemyError:
        # Engine/driver messages may include hostnames or submitted data.
        print("Database operation failed and was rolled back; check connectivity, migrations and data constraints.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
