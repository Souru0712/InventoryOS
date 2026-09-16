"""extraction corrections

Revision ID: 9bbbef6381cc
Revises: 74ceec6f42d3
"""
from alembic import op
import sqlalchemy as sa

revision = '9bbbef6381cc'
down_revision = '74ceec6f42d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'extraction_corrections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('document_line_id', sa.Integer(), nullable=True),
        sa.Column('field', sa.String(length=24), nullable=False),
        sa.Column('ai_value', sa.String(length=300), nullable=True),
        sa.Column('corrected_value', sa.String(length=300), nullable=True),
        sa.Column('corrected_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.ForeignKeyConstraint(['document_line_id'], ['document_lines.id']),
        sa.ForeignKeyConstraint(['corrected_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_extraction_corrections_tenant_id', 'extraction_corrections', ['tenant_id'])
    op.create_index('ix_extraction_corrections_document_id', 'extraction_corrections', ['document_id'])
    op.create_index(
        'ix_extraction_corrections_tenant_field', 'extraction_corrections', ['tenant_id', 'field']
    )


def downgrade() -> None:
    op.drop_index('ix_extraction_corrections_tenant_field', table_name='extraction_corrections')
    op.drop_index('ix_extraction_corrections_document_id', table_name='extraction_corrections')
    op.drop_index('ix_extraction_corrections_tenant_id', table_name='extraction_corrections')
    op.drop_table('extraction_corrections')
