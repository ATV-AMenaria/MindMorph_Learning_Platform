"""add graph_cache

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 12:43:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002_rag_chunks'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "graph_cache",
        sa.Column("cache_key",      sa.Text, primary_key=True),
        sa.Column("canonical_topic",sa.Text, nullable=False),
        sa.Column("query_text",     sa.Text, nullable=False),
        sa.Column("skill_graph",    postgresql.JSONB, nullable=False),
        sa.Column("hit_count",      sa.Integer, server_default="0"),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_hit_at",    sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

def downgrade() -> None:
    op.drop_table("graph_cache")
