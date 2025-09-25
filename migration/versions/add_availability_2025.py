"""add availability table\n\nRevision ID: add_availability_2025\nRevises: 037e1296f152\nCreate Date: 2025-09-25\n"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'availability',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('faculty_id', sa.Integer(), sa.ForeignKey('faculties.id'), nullable=False),
        sa.Column('date', sa.String(length=20), nullable=False),
        sa.Column('time_slot', sa.String(length=20), nullable=False),
        sa.Column('is_available', sa.Boolean(), nullable=False, default=True)
    )

def downgrade():
    op.drop_table('availability')
