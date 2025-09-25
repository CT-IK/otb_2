"""
Alembic migration: добавление уникального ограничения на slot_limits (faculty_id, date, time_slot)
"""
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
