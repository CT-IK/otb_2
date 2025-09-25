"""add unique constraint to slot_limits

Revision ID: add_unique_slot_limits_faculty_date_time
Revises: add_slot_limits_2025
Create Date: 2025-09-26
"""
revision = 'add_unique_slot_limits_faculty_date_time'
down_revision = 'add_slot_limits_2025'
branch_labels = None
depends_on = None

from alembic import op

def upgrade():
    op.create_unique_constraint(
        "uq_slot_limits_faculty_date_time",
        "slot_limits",
        ["faculty_id", "date", "time_slot"]
    )

def downgrade():
    op.drop_constraint(
        "uq_slot_limits_faculty_date_time",
        "slot_limits",
        type_="unique"
    )