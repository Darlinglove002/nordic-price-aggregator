"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("normalized_name", sa.String(512), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_products_source_external"),
    )
    op.create_index("ix_products_normalized_name", "products", ["normalized_name"])
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("items_saved", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
    )
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sync_run_id",
            sa.Integer(),
            sa.ForeignKey("sync_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_local", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_snapshots_product_scraped", "price_snapshots", ["product_id", "scraped_at"])
    op.create_table(
        "subscribers",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("subscribers")
    op.drop_index("ix_snapshots_product_scraped", table_name="price_snapshots")
    op.drop_table("price_snapshots")
    op.drop_table("sync_runs")
    op.drop_index("ix_products_normalized_name", table_name="products")
    op.drop_table("products")
    op.drop_table("sources")
