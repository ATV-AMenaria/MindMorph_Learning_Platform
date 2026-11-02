"""Private versioned assessment packages and durable accepted attempts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_grading_records"
down_revision = "0005_grounding_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("grading_records",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id", "record_id"))
    op.create_index("ix_grading_records_kind", "grading_records", ["kind"])


def downgrade():
    op.drop_index("ix_grading_records_kind", table_name="grading_records")
    op.drop_table("grading_records")
