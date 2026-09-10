from alembic import context
from runnerx.db import database_url, get_engine
from runnerx.models import Base

if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
