"""Retain Markdown heading paths, original line ranges and parsing notices."""
import sqlalchemy as sa
from alembic import op

revision = "007_chunk_locator"
down_revision = "006_operations"


def upgrade():
    op.add_column("chunks", sa.Column("locator", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
