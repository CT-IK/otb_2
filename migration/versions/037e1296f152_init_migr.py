"""Init migr

Revision ID: 037e1296f152
Revises:
Create Date: 2025-09-25 13:38:01.496241

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '037e1296f152'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Создаём таблицы без FK между faculties и users
    op.create_table('faculties',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('google_sheet_url', sa.Text(), nullable=True),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_table('slots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=True),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('tg_id', sa.String(length=100), nullable=True),
        sa.Column('is_candidate', sa.Boolean(), nullable=False),
        sa.Column('is_sobeser', sa.Boolean(), nullable=False),
        sa.Column('is_admin_faculty', sa.Boolean(), nullable=False),
        sa.Column('faculty_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_id')
    )
    op.create_table('candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('vk_id', sa.String(length=100), nullable=False),
        sa.Column('tg_id', sa.String(length=100), nullable=True),
        sa.Column('faculty_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_id'),
        sa.UniqueConstraint('vk_id')
    )

    # 2. Добавляем внешние ключи после создания таблиц
    op.create_foreign_key(
        'fk_users_faculty_id_faculties',
        'users', 'faculties',
        ['faculty_id'], ['id']
    )
    op.create_foreign_key(
        'fk_faculties_admin_id_users',
        'faculties', 'users',
        ['admin_id'], ['id']
    )
    op.create_foreign_key(
        'fk_candidates_faculty_id_faculties',
        'candidates', 'faculties',
        ['faculty_id'], ['id']
    )

def downgrade() -> None:
    # Удаляем внешние ключи (если потребуется явно)
    op.drop_table('candidates')
    op.drop_table('users')
    op.drop_table('slots')
    op.drop_table('faculties')
