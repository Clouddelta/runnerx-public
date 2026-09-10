"""Verify ownership in MySQL itself, independently of API validation."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from runnerx.models import Bootcamp, BootcampGroup, Registration, Runner, TrainingSession

pytestmark = pytest.mark.integration


def test_mysql_rejects_cross_tenant_registration(db, tenant, other_tenant, bootcamp):
    outsider = Runner(tenant_id=other_tenant.id, external_id="outside", full_name="Synthetic Outsider")
    db.add(outsider)
    db.commit()
    db.add(Registration(tenant_id=tenant.id, bootcamp_id=bootcamp.id, runner_id=outsider.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_mysql_rejects_group_from_another_camp(db, tenant, bootcamp):
    runner = Runner(tenant_id=tenant.id, external_id="one", full_name="Synthetic One")
    another = Bootcamp(tenant_id=tenant.id, code="another", name="Another Camp",
                      start_date=date(2026, 1, 5), end_date=date(2026, 2, 1))
    db.add_all([runner, another])
    db.commit()
    group = BootcampGroup(tenant_id=tenant.id, bootcamp_id=another.id, name="A")
    db.add(group)
    db.commit()
    db.add(Registration(tenant_id=tenant.id, bootcamp_id=bootcamp.id, runner_id=runner.id, group_id=group.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_mysql_requires_registration_before_training(db, tenant, bootcamp):
    runner = Runner(tenant_id=tenant.id, external_id="one", full_name="Synthetic One")
    db.add(runner)
    db.commit()
    db.add(TrainingSession(tenant_id=tenant.id, bootcamp_id=bootcamp.id, runner_id=runner.id,
                           session_date=date(2026, 1, 6), distance_km=5))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
