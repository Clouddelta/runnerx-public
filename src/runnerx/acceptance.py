"""Real HTTP and CLI acceptance for local Docker, with scoped QA cleanup."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4
from sqlalchemy import delete, select
from runnerx.config import get_settings
from runnerx.db import get_engine
from runnerx.models import Base, Tenant

class AcceptanceError(RuntimeError):
    pass

def require(condition, message='Acceptance assertion failed'):
    if not condition:
        raise AcceptanceError(str(message))

def validate_target(base_url):
    require(get_settings().app_env in {'local', 'test'}, 'Acceptance is enabled only for local/test environments')
    value = urlsplit(base_url)
    require(value.scheme == 'http' and value.hostname in {'api', 'localhost', '127.0.0.1', '::1'} and (not value.username) and (not value.password) and (value.path in {'', '/'}) and (not value.query) and (not value.fragment), 'Use the local Compose API or a loopback HTTP address')
    try:
        value.port
    except ValueError as exc:
        raise AcceptanceError('Invalid API port') from exc
    return base_url.rstrip('/')

def database_fingerprint(excluded_ids=()):
    """Hash rows without writing data or credential hashes to reports."""
    result = {}
    with get_engine().connect() as connection:
        for table in Base.metadata.sorted_tables:
            query = select(table).order_by(table.c.id)
            if excluded_ids:
                owner = table.c.id if table.name == 'tenants' else table.c.tenant_id
                query = query.where(owner.not_in(excluded_ids))
            digest, count = (hashlib.sha256(), 0)
            for row in connection.execute(query).mappings():
                digest.update(json.dumps(dict(row), sort_keys=True, default=str).encode('utf-8') + b'\n')
                count += 1
            result[table.name] = {'count': count, 'sha256': digest.hexdigest()}
    return result

def cleanup_tenants(expected):
    """Delete only exact UUID+slug pairs provisioned by this QA run."""
    if not expected:
        return
    with get_engine().begin() as connection:
        identities = []
        for identity, slug in expected.items():
            require(slug.startswith('qa-business-'), 'Cleanup only accepts QA tenant identities')
            actual = connection.execute(select(Tenant.slug).where(Tenant.id == identity)).scalar_one_or_none()
            require(actual == slug, 'QA tenant identity changed; cleanup aborted')
            identities.append(identity)
        for table in reversed(Base.metadata.sorted_tables):
            owner = table.c.id if table.name == 'tenants' else table.c.tenant_id
            connection.execute(delete(table).where(owner.in_(identities)))

def run_acceptance(base_url='http://api:8000'):
    BASE = validate_target(base_url)
    RUN = 'qa-business-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
    CAMP = 'qa-2026'
    report = {'run': RUN, 'base_url': BASE, 'checks': [], 'passed': False, 'cleanup': 'pending'}
    credentials = {}
    planned_slugs = []
    http_statuses = Counter()
    temporary = TemporaryDirectory(prefix=RUN + '-')
    OUT = Path(temporary.name)
    REMOTE = str(OUT)
    existing_before = None

    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, '-m', 'runnerx.cli', *args], capture_output=True, text=True, encoding='utf-8', timeout=90)
        require(result.returncode == expected, ('CLI exit code', args[0], expected, result.returncode, result.stderr))
        return json.loads(result.stdout)

    def http(method, path, key=None, body=None, expected=200):
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        encoded = None if body is None else json.dumps(body).encode()
        if encoded is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(BASE + path, data=encoded, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, raw = (response.status, response.read())
        except urllib.error.HTTPError as error:
            status, raw = (error.code, error.read())
        http_statuses[str(status)] += 1
        require(status == expected, (method, path, 'expected', expected, 'received', status, raw.decode()[:500]))
        return json.loads(raw) if raw else None

    def records(key, resource):
        items, page = ([], 1)
        while True:
            payload = http('GET', f'/api/v1/{resource}?page={page}&page_size=100', key)
            items.extend(payload['items'])
            if len(items) >= payload['total']:
                require(len(items) == payload['total'], "len(items) == payload['total']")
                return sorted(items, key=lambda item: item['id'])
            page += 1

    def snapshot(key, audits=True):
        resources = ['runners', 'registrations', 'sessions', 'groups', 'bootcamps'] + (['imports'] if audits else [])
        return {resource: records(key, resource) for resource in resources}

    def imported(tenant, file, kind='registrations', dry=False, expected=0):
        args = ['import', '--tenant', tenant['slug'], '--bootcamp', CAMP, '--kind', kind, '--file', REMOTE + '/' + file]
        if dry:
            args += ['--dry-run']
        value = cli(*args, expected=expected)
        (OUT / (tenant['slug'][-1] + '-' + file + ('.dry' if dry else '') + '.report.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')
        return value

    def checked(name, **details):
        report['checks'].append({'name': name, 'passed': True, **details})
        print('PASS:', name, flush=True, file=sys.stderr)
    try:
        existing_before = database_fingerprint()
        http('GET', '/ready')
        http('GET', '/api/v1/runners', expected=401)
        http('GET', '/api/v1/runners', 'invalid-business-check-token', expected=401)
        for label in ['a', 'b']:
            slug = RUN + '-' + label
            with get_engine().connect() as connection:
                require(connection.execute(select(Tenant.id).where(Tenant.slug == slug)).first() is None, 'QA slug already exists')
            # Register the proven-new slug before the child may commit. Cleanup
            # still finds it if CLI output is malformed or the child times out.
            planned_slugs.append(slug)
            tenant = cli('create-tenant', '--slug', slug, '--name', 'Synthetic Business QA ' + label.upper())
            credentials[label] = tenant
            tenant['camp'] = http('POST', '/api/v1/bootcamps', tenant['api_key'], {'code': CAMP, 'name': 'Synthetic Business QA Camp ' + label.upper(), 'start_date': '2026-01-05', 'end_date': '2026-02-01'}, expected=201)
        a, b = (credentials['a'], credentials['b'])
        ka, kb = (a['api_key'], b['api_key'])
        report['tenants'] = {label: {'slug': value['slug'], 'tenant_id': value['tenant_id'], 'bootcamp_id': value['camp']['id']} for label, value in credentials.items()}
        cli('generate-sample', '--output', REMOTE, '--runners', '3', '--seed', '42')
        first = imported(a, 'registrations.xlsx')
        sessions = imported(a, 'sessions.csv', 'sessions')
        require(first['status'] == sessions['status'] == 'committed', "first['status'] == sessions['status'] == 'committed'")
        require(first['inserted_count'] == 3 and sessions['inserted_count'] == 36, "first['inserted_count'] == 3 and sessions['inserted_count'] == 36")
        group = http('POST', '/api/v1/groups', ka, {'bootcamp_id': a['camp']['id'], 'name': 'QA training group'}, expected=201)
        initial = snapshot(ka)
        runner = next((item for item in initial['runners'] if item['external_id'] == 'SYN0001'))
        registration = next((item for item in initial['registrations'] if item['runner_id'] == runner['id']))
        http('PATCH', '/api/v1/registrations/' + registration['id'], ka, {'group_id': group['id']})
        initial = snapshot(ka)
        require([len(initial[name]) for name in ['runners', 'registrations', 'sessions', 'imports']] == [3, 3, 36, 2], "[len(initial[name]) for name in ['runners', 'registrations', 'sessions', 'imports']] == [3, 3, 36, 2]")
        checked('Fresh QA tenant import', runners=3, registrations=3, sessions=36, imports=2)
        editor = "from pathlib import Path\nfrom openpyxl import load_workbook\nfolder = Path({folder!r})\nbook = load_workbook(folder / 'registrations.xlsx')\nsheet = book.active\nassert sheet['A2'].value == 'SYN0001'\nsheet['B2'] = 'QA Updated Runner'\nsheet['F2'] = 66.25\nsheet['G2'] = '45:30'\nbook.save(folder / 'updated.xlsx')\nsheet['B2'] = 'SHOULD NOT BE SAVED'\nsheet['G3'] = '00:99:00'\nnew_row = [cell.value for cell in sheet[4]]\nnew_row[0] = 'QA-NEW'\nnew_row[1] = 'QA valid new runner'\nsheet.append(new_row)\nbook.save(folder / 'mixed-invalid.xlsx')\nbook.close()\n".format(folder=REMOTE)
        result = subprocess.run([sys.executable, '-'], input=editor, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'Could not edit generated workbook')
        dry = imported(a, 'updated.xlsx', dry=True)
        require(dry['status'] == 'validated' and dry['batch_id'] is None, "dry['status'] == 'validated' and dry['batch_id'] is None")
        require(snapshot(ka) == initial, 'snapshot(ka) == initial')
        checked('Valid dry run makes no business or audit changes')
        updated = imported(a, 'updated.xlsx')
        require((updated['status'], updated['inserted_count'], updated['updated_count']) == ('committed', 0, 3), "(updated['status'], updated['inserted_count'], updated['updated_count']) == ('committed', 0, 3)")
        after_update = snapshot(ka)
        for resource in ['runners', 'registrations', 'sessions', 'groups', 'bootcamps']:
            require([item['id'] for item in after_update[resource]] == [item['id'] for item in initial[resource]], resource)
        new_runner = http('GET', '/api/v1/runners/' + runner['id'], ka)
        new_registration = http('GET', '/api/v1/registrations/' + registration['id'], ka)
        require(new_runner['full_name'] == 'QA Updated Runner' and new_runner['weight_kg'] == 66.25, "new_runner['full_name'] == 'QA Updated Runner' and new_runner['weight_kg'] == 66.25")
        require(new_registration['test_10k_sec'] == 2730 and new_registration['group_id'] == group['id'], "new_registration['test_10k_sec'] == 2730 and new_registration['group_id'] == group['id']")
        require(after_update['sessions'] == initial['sessions'], "after_update['sessions'] == initial['sessions']")
        checked('Edited Excel updates existing records and preserves IDs', external_id='SYN0001', full_name='QA Updated Runner', weight_kg=66.25, test_10k_sec=2730, inserted=0, existing_rows_processed=3)
        replay = imported(a, 'updated.xlsx')
        require(replay['status'] == 'duplicate' and replay['batch_id'] == updated['batch_id'] and (replay['inserted_count'] == replay['updated_count'] == 0), "replay['status'] == 'duplicate' and replay['batch_id'] == updated['batch_id'] and (replay['inserted_count'] == replay['updated_count'] == 0)")
        require(snapshot(ka) == after_update, 'snapshot(ka) == after_update')
        checked('Repeated edited file is idempotent; no extra audit')
        rejected_dry = imported(a, 'mixed-invalid.xlsx', dry=True, expected=2)
        require(rejected_dry['status'] == 'rejected' and rejected_dry['batch_id'] is None, "rejected_dry['status'] == 'rejected' and rejected_dry['batch_id'] is None")
        require(snapshot(ka) == after_update, 'snapshot(ka) == after_update')
        checked('Invalid dry run exits 2 without any writes')
        rejected = imported(a, 'mixed-invalid.xlsx', expected=2)
        require(rejected['status'] == 'rejected' and rejected['row_count'] == rejected['rejected_count'] == 4, "rejected['status'] == 'rejected' and rejected['row_count'] == rejected['rejected_count'] == 4")
        require(rejected['accepted_count'] == rejected['inserted_count'] == rejected['updated_count'] == 0, "rejected['accepted_count'] == rejected['inserted_count'] == rejected['updated_count'] == 0")
        require(any((error['row'] == 3 and error['field'] == 'test_10k_sec' for error in rejected['errors'])), "any((error['row'] == 3 and error['field'] == 'test_10k_sec' for error in rejected['errors']))")
        require(snapshot(ka, audits=False) == {key: value for key, value in after_update.items() if key != 'imports'}, "snapshot(ka, audits=False) == {key: value for key, value in after_update.items() if key != 'imports'}")
        require(http('GET', '/api/v1/runners?external_id=QA-NEW', ka)['total'] == 0, "http('GET', '/api/v1/runners?external_id=QA-NEW', ka)['total'] == 0")
        audit = http('GET', '/api/v1/imports/' + rejected['batch_id'], ka)
        require(audit['status'] == 'rejected' and audit['errors'] == rejected['errors'], "audit['status'] == 'rejected' and audit['errors'] == rejected['errors']")
        require(len(records(ka, 'imports')) == 4, "len(records(ka, 'imports')) == 4")
        checked('Mixed valid/invalid batch rejected atomically with an audit', rejected_rows=4, error_row=3, field='test_10k_sec', old_record_unchanged=True, new_record_absent=True)
        for method, path in [('POST', '/api/v1/imports'), ('PATCH', '/api/v1/imports/' + audit['id']), ('DELETE', '/api/v1/imports/' + audit['id'])]:
            http(method, path, ka, {} if method != 'DELETE' else None, expected=405)
        checked('Import audits cannot be mutated via the API')
        b_before = snapshot(kb)
        require(len(b_before['bootcamps']) == 1 and b_before['bootcamps'][0]['id'] == b['camp']['id'], "len(b_before['bootcamps']) == 1 and b_before['bootcamps'][0]['id'] == b['camp']['id']")
        require(all((not value for key, value in b_before.items() if key != 'bootcamps')), "all((not value for key, value in b_before.items() if key != 'bootcamps'))")
        saved_a = snapshot(ka)
        foreign_objects = [('runners', runner['id'], {'full_name': 'FORBIDDEN'}), ('bootcamps', a['camp']['id'], {'name': 'FORBIDDEN'}), ('groups', group['id'], {'name': 'FORBIDDEN'}), ('registrations', registration['id'], {'experience_text': 'FORBIDDEN'}), ('sessions', initial['sessions'][0]['id'], {'notes': 'FORBIDDEN'})]
        for resource, identity, payload in foreign_objects:
            for method in ['GET', 'PATCH', 'DELETE']:
                http(method, f'/api/v1/{resource}/{identity}', kb, payload if method == 'PATCH' else None, expected=404)
        http('GET', '/api/v1/bootcamps/' + a['camp']['id'] + '/stats', kb, expected=404)
        http('GET', '/api/v1/imports/' + audit['id'], kb, expected=404)
        for resource in ['groups', 'registrations', 'sessions', 'imports']:
            require(http('GET', f'/api/v1/{resource}?bootcamp_id=' + a['camp']['id'], kb)['total'] == 0, "http('GET', f'/api/v1/{resource}?bootcamp_id=' + a['camp']['id'], kb)['total'] == 0")
        require(http('GET', '/api/v1/sessions?runner_id=' + runner['id'], kb)['total'] == 0, "http('GET', '/api/v1/sessions?runner_id=' + runner['id'], kb)['total'] == 0")
        require(snapshot(ka) == saved_a and snapshot(kb) == b_before, 'snapshot(ka) == saved_a and snapshot(kb) == b_before')
        checked('Cross-tenant details, writes, statistics and filters are isolated', foreign_detail_and_mutation_status=404)
        b_import = imported(b, 'registrations.xlsx')
        require(b_import['status'] == 'committed' and b_import['inserted_count'] == 3 and (b_import['batch_id'] != first['batch_id']), "b_import['status'] == 'committed' and b_import['inserted_count'] == 3 and (b_import['batch_id'] != first['batch_id'])")
        b_runner = http('GET', '/api/v1/runners?external_id=SYN0001', kb)['items'][0]
        require(b_runner['id'] != runner['id'] and b_runner['full_name'] == 'Sample Runner0001', "b_runner['id'] != runner['id'] and b_runner['full_name'] == 'Sample Runner0001'")
        require(http('GET', '/api/v1/runners/' + runner['id'], ka)['full_name'] == 'QA Updated Runner', "http('GET', '/api/v1/runners/' + runner['id'], ka)['full_name'] == 'QA Updated Runner'")
        checked('Same external IDs and file bytes are independent per tenant', external_id='SYN0001', tenant_b_inserted=3)
        b_registration = next((item for item in records(kb, 'registrations') if item['runner_id'] == b_runner['id']))
        b_after_import = snapshot(kb)
        http('POST', '/api/v1/registrations', kb, {'bootcamp_id': b['camp']['id'], 'runner_id': runner['id']}, expected=404)
        http('POST', '/api/v1/groups', kb, {'bootcamp_id': a['camp']['id'], 'name': 'FORBIDDEN'}, expected=404)
        http('PATCH', '/api/v1/registrations/' + b_registration['id'], kb, {'runner_id': runner['id']}, expected=404)
        for field, value in [('tenant_id', a['tenant_id']), ('id', runner['id']), ('unknown', 'forbidden')]:
            http('POST', '/api/v1/runners', kb, {'external_id': 'QA-INJECT', 'full_name': 'FORBIDDEN', field: value}, expected=422)
            http('PATCH', '/api/v1/runners/' + b_runner['id'], kb, {field: value}, expected=422)
        require(snapshot(kb) == b_after_import and snapshot(ka) == saved_a, 'snapshot(kb) == b_after_import and snapshot(ka) == saved_a')
        checked('Foreign associations and client-supplied ownership are rejected', foreign_reference_status=404, forbidden_field_status=422)
        require(database_fingerprint([t['tenant_id'] for t in credentials.values()]) == existing_before, 'Existing tenant data changed')
        checked('Existing tenant data unchanged')
        report['final_counts'] = {label: {key: len(value) for key, value in snapshot(tenant['api_key']).items()} for label, tenant in credentials.items()}
    except Exception as exc:
        report['error'] = str(exc) if isinstance(exc, AcceptanceError) else f'{type(exc).__name__}: check local services and database configuration'
    finally:
        try:
            with get_engine().connect() as connection:
                owned = dict(connection.execute(select(Tenant.id, Tenant.slug).where(Tenant.slug.in_(planned_slugs))).all())
            cleanup_tenants(owned)
            if existing_before is not None:
                require(database_fingerprint() == existing_before, 'Existing records differ after cleanup; run acceptance without concurrent writers')
            report['cleanup'] = 'passed'
        except Exception as exc:
            report['cleanup'] = 'failed'
            report['cleanup_error'] = str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__
        temporary.cleanup()
    report['passed'] = 'error' not in report and report['cleanup'] == 'passed'
    report['http_status_counts'] = dict(sorted(http_statuses.items()))
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description='Run real Docker business acceptance with isolated synthetic tenants and cleanup')
    parser.add_argument('--base-url', default='http://api:8000')
    args = parser.parse_args(argv)
    try:
        result = run_acceptance(args.base_url)
    except Exception as exc:
        result = {'passed': False, 'error': str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1
if __name__ == '__main__':
    raise SystemExit(main())
