from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Source(Base):
    """A shop / catalogue we scrape."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))

    products: Mapped[list[Product]] = relationship(back_populates="source")


class Product(Base):
    """A product as listed by one source. ``normalized_name`` links the same item across shops."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_products_source_external"),
        Index("ix_products_normalized_name", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(512))
    normalized_name: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="products")
    snapshots: Mapped[list[PriceSnapshot]] = relationship(back_populates="product")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(16))  # "schedule" | "manual"
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|partial|failed
    items_saved: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)


class PriceSnapshot(Base):
    """Append-only price history: one row per product per sync run."""

    __tablename__ = "price_snapshots"
    __table_args__ = (Index("ix_snapshots_product_scraped", "product_id", "scraped_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id", ondelete="CASCADE"))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    price_local: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    price_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    in_stock: Mapped[bool] = mapped_column(Boolean)

    product: Mapped[Product] = relationship(back_populates="snapshots")


class Subscriber(Base):
    """Telegram chats that receive the scheduled digest (/alerts)."""

    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
