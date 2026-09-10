"""Acceptance safeguards: local scope and cleanup after partial provisioning."""
import io
import subprocess
import urllib.error

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from runnerx import acceptance
from runnerx.models import Tenant


@pytest.mark.parametrize('url', ['https://example.com', 'http://example.com', 'http://api:8000/path', 'http://user:pass@api:8000', 'http://api:bad', 'http://api:8000?x=1'])
def test_rejects_nonlocal_or_ambiguous_target(monkeypatch, url):
    monkeypatch.setenv('APP_ENV', 'local')
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.validate_target(url)


def test_production_refused_before_database_access(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(acceptance, 'get_engine', lambda: pytest.fail('Unexpected database access'))
    with pytest.raises(acceptance.AcceptanceError, match='local/test'):
        acceptance.run_acceptance()


@pytest.mark.integration
def test_cleanup_requires_exact_identity_and_preserves_existing_tenant(db, engine, tenant, monkeypatch):
    monkeypatch.setattr(acceptance, 'get_engine', lambda: engine)
    before = acceptance.database_fingerprint()
    qa = Tenant(slug='qa-business-cleanup', name='Synthetic cleanup check')
    db.add(qa)
    db.commit()
    with pytest.raises(acceptance.AcceptanceError, match='identity changed'):
        acceptance.cleanup_tenants({qa.id: 'qa-business-wrong'})
    assert db.scalar(select(Tenant).where(Tenant.id == qa.id)) is not None
    acceptance.cleanup_tenants({qa.id: qa.slug})
    assert acceptance.database_fingerprint() == before


@pytest.mark.integration
@pytest.mark.parametrize('failure', ['invalid_json', 'timeout'])
def test_cleanup_after_cli_committed_without_usable_output(db, engine, tenant, monkeypatch, failure):
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setattr(acceptance, 'get_engine', lambda: engine)
    before = acceptance.database_fingerprint()

    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"status":"ok"}'

    def http(request, **kwargs):
        if request.full_url.endswith('/ready'):
            return Ready()
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))

    def provisioning(command, **kwargs):
        assert 'create-tenant' in command
        slug = command[command.index('--slug') + 1]
        with Session(engine) as isolated:
            isolated.add(Tenant(slug=slug, name='Committed before output failure'))
            isolated.commit()
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 90)
        return subprocess.CompletedProcess(command, 0, stdout='invalid JSON', stderr='')

    monkeypatch.setattr(acceptance.urllib.request, 'urlopen', http)
    monkeypatch.setattr(acceptance.subprocess, 'run', provisioning)
    report = acceptance.run_acceptance()
    assert report['passed'] is False
    assert report['cleanup'] == 'passed'
    assert acceptance.database_fingerprint() == before
