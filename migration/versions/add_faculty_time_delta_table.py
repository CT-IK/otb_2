"""
add faculty_time_delta table

Revision ID: add_faculty_time_delta_table
Revises: interview_registrations_2025
Create Date: 2025-01-27
"""
revision = 'add_faculty_time_delta_table'
down_revision = 'interview_registrations_2025'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'faculty_time_deltas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('faculty_id', sa.Integer(), sa.ForeignKey('faculties.id'), nullable=False),
        sa.Column('hours_before_interview', sa.Integer(), nullable=False, server_default='4'),
        sa.UniqueConstraint('faculty_id')
    )

def downgrade():
    op.drop_table('faculty_time_deltas')
