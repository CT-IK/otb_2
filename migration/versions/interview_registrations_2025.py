"""
add interview_registrations table

Revision ID: interview_registrations_2025
Revises: a1b2c3d4e5f6
Create Date: 2025-09-26
"""
revision = 'interview_registrations_2025'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'interview_registrations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('faculty_id', sa.Integer(), sa.ForeignKey('faculties.id'), nullable=False),
        sa.Column('date', sa.String(length=20), nullable=False),
        sa.Column('time_slot', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('canceled', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )

def downgrade():
    op.drop_table('interview_registrations')
