"""Each bearer key belongs to one tenant. Only SHA-256 digests are stored."""
import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from runnerx.db import get_session
from runnerx.models import ApiKey, Tenant

bearer = HTTPBearer(auto_error=False)


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> Tenant:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "A tenant API key is required", headers={"WWW-Authenticate": "Bearer"})
    tenant = db.scalar(
        select(Tenant).join(ApiKey, ApiKey.tenant_id == Tenant.id).where(
            ApiKey.key_hash == hash_api_key(credentials.credentials), ApiKey.is_active.is_(True)
        )
    )
    if tenant is None:
        raise HTTPException(401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"})
    return tenant
