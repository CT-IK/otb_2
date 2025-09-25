"""
Alembic migration: добавление уникального ограничения на slot_limits (faculty_id, date, time_slot)
"""
from alembic import op

"""
    op.create_unique_constraint(
