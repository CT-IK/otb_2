"""add slot_limits table\n\nRevision ID: add_slot_limits_2025\nRevises: add_availability_2025\nCreate Date: 2025-09-25\n"""
revision = 'add_slot_limits_2025'
down_revision = 'add_availability_2025'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'slot_limits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('faculty_id', sa.Integer(), sa.ForeignKey('faculties.id'), nullable=False),
        sa.Column('date', sa.String(length=20), nullable=False),
        sa.Column('time_slot', sa.String(length=20), nullable=False),
        sa.Column('limit', sa.Integer(), nullable=False, default=0)
    )

def downgrade():
    op.drop_table('slot_limits')
