"""initial tenant isolated schema"""
from alembic import op
import sqlalchemy as sa

revision = '5c33134f5a57'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('tenants',
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('api_keys',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash')
    )
    op.create_table('bootcamps',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('end_date >= start_date', name='ck_bootcamp_dates'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_bootcamp_code'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_bootcamp_tenant_id')
    )
    op.create_index('ix_bootcamp_tenant_created', 'bootcamps', ['tenant_id', 'created_at', 'id'], unique=False)
    op.create_table('runners',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('external_id', sa.String(length=80), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('gender', sa.String(length=1), nullable=False),
    sa.Column('birth_year', sa.Integer(), nullable=True),
    sa.Column('height_cm', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('weight_kg', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("gender IN ('M','F','O','U')", name='ck_runner_gender'),
    sa.CheckConstraint('birth_year IS NULL OR birth_year BETWEEN 1800 AND 2200', name='ck_runner_year'),
    sa.CheckConstraint('height_cm IS NULL OR height_cm > 0', name='ck_runner_height'),
    sa.CheckConstraint('weight_kg IS NULL OR weight_kg > 0', name='ck_runner_weight'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'external_id', name='uq_runner_external'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_runner_tenant_id')
    )
    op.create_index('ix_runner_tenant_created', 'runners', ['tenant_id', 'created_at', 'id'], unique=False)
    op.create_table('bootcamp_groups',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('bootcamp_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('target_time_min_sec', sa.Integer(), nullable=True),
    sa.Column('target_time_max_sec', sa.Integer(), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('target_time_max_sec IS NULL OR target_time_max_sec > 0', name='ck_group_max'),
    sa.CheckConstraint('target_time_min_sec IS NULL OR target_time_max_sec IS NULL OR target_time_min_sec <= target_time_max_sec', name='ck_group_range'),
    sa.CheckConstraint('target_time_min_sec IS NULL OR target_time_min_sec > 0', name='ck_group_min'),
    sa.ForeignKeyConstraint(['tenant_id', 'bootcamp_id'], ['bootcamps.tenant_id', 'bootcamps.id'], name='fk_group_bootcamp', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'bootcamp_id', 'id', name='uq_group_scope'),
    sa.UniqueConstraint('tenant_id', 'bootcamp_id', 'name', name='uq_group_name')
    )
    op.create_table('import_batches',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('bootcamp_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.Column('rejected_count', sa.Integer(), nullable=False),
    sa.Column('inserted_count', sa.Integer(), nullable=False),
    sa.Column('updated_count', sa.Integer(), nullable=False),
    sa.Column('errors', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('registrations','sessions')", name='ck_import_kind'),
    sa.CheckConstraint("status IN ('committed','rejected')", name='ck_import_status'),
    sa.ForeignKeyConstraint(['tenant_id', 'bootcamp_id'], ['bootcamps.tenant_id', 'bootcamps.id'], name='fk_import_bootcamp', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_import_content', 'import_batches', ['tenant_id', 'bootcamp_id', 'kind', 'sha256', 'status'], unique=False)
    op.create_table('registrations',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('bootcamp_id', sa.String(length=36), nullable=False),
    sa.Column('runner_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.String(length=36), nullable=True),
    sa.Column('test_10k_sec', sa.Integer(), nullable=True),
    sa.Column('fm_best_sec', sa.Integer(), nullable=True),
    sa.Column('prep_mileage_km', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('experience_text', sa.Text(), nullable=True),
    sa.Column('raw_data', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('fm_best_sec IS NULL OR fm_best_sec > 0', name='ck_registration_fm'),
    sa.CheckConstraint('prep_mileage_km IS NULL OR prep_mileage_km >= 0', name='ck_registration_mileage'),
    sa.CheckConstraint('test_10k_sec IS NULL OR test_10k_sec > 0', name='ck_registration_10k'),
    sa.ForeignKeyConstraint(['tenant_id', 'bootcamp_id', 'group_id'], ['bootcamp_groups.tenant_id', 'bootcamp_groups.bootcamp_id', 'bootcamp_groups.id'], name='fk_registration_group', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'bootcamp_id'], ['bootcamps.tenant_id', 'bootcamps.id'], name='fk_registration_bootcamp', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'runner_id'], ['runners.tenant_id', 'runners.id'], name='fk_registration_runner', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'bootcamp_id', 'runner_id', name='uq_registration_runner')
    )
    op.create_index('ix_registration_runner', 'registrations', ['tenant_id', 'runner_id'], unique=False)
    op.create_table('training_sessions',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('bootcamp_id', sa.String(length=36), nullable=False),
    sa.Column('runner_id', sa.String(length=36), nullable=False),
    sa.Column('session_date', sa.Date(), nullable=False),
    sa.Column('distance_km', sa.Numeric(precision=8, scale=3), nullable=False),
    sa.Column('pace_sec_per_km', sa.Integer(), nullable=True),
    sa.Column('resting_hr', sa.Integer(), nullable=True),
    sa.Column('fatigue_level', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('distance_km > 0', name='ck_session_distance'),
    sa.CheckConstraint('fatigue_level IS NULL OR fatigue_level BETWEEN 1 AND 5', name='ck_session_fatigue'),
    sa.CheckConstraint('pace_sec_per_km IS NULL OR pace_sec_per_km > 0', name='ck_session_pace'),
    sa.CheckConstraint('resting_hr IS NULL OR resting_hr BETWEEN 1 AND 300', name='ck_session_hr'),
    sa.ForeignKeyConstraint(['tenant_id', 'bootcamp_id', 'runner_id'], ['registrations.tenant_id', 'registrations.bootcamp_id', 'registrations.runner_id'], name='fk_session_registration', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'bootcamp_id', 'runner_id', 'session_date', name='uq_session_day')
    )
    op.create_index('ix_session_camp_date', 'training_sessions', ['tenant_id', 'bootcamp_id', 'session_date'], unique=False)
    op.create_index('ix_session_runner_date', 'training_sessions', ['tenant_id', 'runner_id', 'session_date'], unique=False)

def downgrade():
    op.drop_index('ix_session_runner_date', table_name='training_sessions')
    op.drop_index('ix_session_camp_date', table_name='training_sessions')
    op.drop_table('training_sessions')
    op.drop_index('ix_registration_runner', table_name='registrations')
    op.drop_table('registrations')
    op.drop_index('ix_import_content', table_name='import_batches')
    op.drop_table('import_batches')
    op.drop_table('bootcamp_groups')
    op.drop_index('ix_runner_tenant_created', table_name='runners')
    op.drop_table('runners')
    op.drop_index('ix_bootcamp_tenant_created', table_name='bootcamps')
    op.drop_table('bootcamps')
    op.drop_table('api_keys')
    op.drop_table('tenants')
