"""Keep external runner identifiers consistent with Python's exact matching."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "5c33134f5a57"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("runners", "external_id", existing_type=sa.String(80),
                    type_=sa.String(80, collation="utf8mb4_bin"), existing_nullable=False)


def downgrade():
    # A downgrade can fail if existing IDs differ only in case; resolve those
    # identifiers deliberately before asking MySQL to rebuild its unique index.
    op.alter_column("runners", "external_id", existing_type=sa.String(80, collation="utf8mb4_bin"),
                    type_=sa.String(80, collation="utf8mb4_unicode_ci"), existing_nullable=False)
