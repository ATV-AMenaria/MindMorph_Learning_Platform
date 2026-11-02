"""Join authentication and grading migrations without rewriting applied history."""

revision = "0007_merge_auth_grading"
down_revision = ("0004", "0006_grading_records")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
