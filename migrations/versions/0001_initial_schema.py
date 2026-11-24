"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-24 20:12:33.329366
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('price_list_versions',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('version', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('company', sa.Text(), nullable=True),
    sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('manual_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_list_versions')),
    sa.UniqueConstraint('content_hash', name=op.f('uq_price_list_versions_content_hash')),
    sa.UniqueConstraint('version', name=op.f('uq_price_list_versions_version'))
    )
    op.create_index('uq_price_list_versions_active', 'price_list_versions', ['is_active'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_table('requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('client_name', sa.Text(), nullable=True),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='extracted', nullable=False),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('urgency', sa.Text(), server_default='standard', nullable=False),
    sa.Column('urgency_evidence', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('extracted', 'quoted', 'manual_estimate', 'needs_clarification')", name=op.f('ck_requests_status')),
    sa.CheckConstraint("urgency IN ('standard', 'urgent_within_week')", name=op.f('ck_requests_urgency')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requests'))
    )
    op.create_table('price_list',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('version_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('unit', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), nullable=True),
    sa.Column('rate', sa.Numeric(), nullable=True),
    sa.Column('material_rate', sa.Numeric(), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint('material_rate >= 0', name=op.f('ck_price_list_material_rate_not_negative')),
    sa.CheckConstraint('rate >= 0', name=op.f('ck_price_list_rate_not_negative')),
    sa.ForeignKeyConstraint(['version_id'], ['price_list_versions.id'], name=op.f('fk_price_list_version_id_price_list_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_list')),
    sa.UniqueConstraint('version_id', 'code', name=op.f('uq_price_list_version_id_code'))
    )
    op.create_table('quotes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('price_list_version_id', sa.Integer(), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('total', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('docx_path', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'ok') = (total IS NOT NULL)", name=op.f('ck_quotes_total_only_if_ok')),
    sa.CheckConstraint("status IN ('ok', 'manual_estimate', 'needs_clarification')", name=op.f('ck_quotes_status')),
    sa.CheckConstraint('total >= 0', name=op.f('ck_quotes_total_not_negative')),
    sa.ForeignKeyConstraint(['price_list_version_id'], ['price_list_versions.id'], name=op.f('fk_quotes_price_list_version_id_price_list_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], name=op.f('fk_quotes_request_id_requests'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quotes')),
    sa.UniqueConstraint('request_id', 'price_list_version_id', 'input_hash', name=op.f('uq_quotes_request_id_price_list_version_id_input_hash'))
    )
    op.create_index(op.f('ix_quotes_price_list_version_id'), 'quotes', ['price_list_version_id'], unique=False)
    op.create_table('request_items',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('service_code', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Numeric(), nullable=True),
    sa.Column('unit', sa.Text(), nullable=True),
    sa.Column('height_m', sa.Numeric(), nullable=True),
    sa.Column('floor', sa.Integer(), nullable=True),
    sa.Column('weight_kg', sa.Numeric(), nullable=True),
    sa.Column('is_dismantle', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint('height_m >= 0', name=op.f('ck_request_items_height_m_not_negative')),
    sa.CheckConstraint('quantity >= 0', name=op.f('ck_request_items_quantity_not_negative')),
    sa.CheckConstraint('weight_kg >= 0', name=op.f('ck_request_items_weight_kg_not_negative')),
    sa.ForeignKeyConstraint(['request_id'], ['requests.id'], name=op.f('fk_request_items_request_id_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_request_items')),
    sa.UniqueConstraint('request_id', 'position', name=op.f('uq_request_items_request_id_position'))
    )


def downgrade() -> None:
    op.drop_table('request_items')
    op.drop_index(op.f('ix_quotes_price_list_version_id'), table_name='quotes')
    op.drop_table('quotes')
    op.drop_table('price_list')
    op.drop_table('requests')
    op.drop_index('uq_price_list_versions_active', table_name='price_list_versions', postgresql_where=sa.text('is_active'))
    op.drop_table('price_list_versions')
