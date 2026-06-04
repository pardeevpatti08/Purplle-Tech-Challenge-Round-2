from collections.abc import AsyncGenerator
import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, MetaData, Numeric, String, Text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import select
from sqlalchemy.sql import func

from app.config import get_settings


class Base(DeclarativeBase):
    metadata = MetaData()


class EventRecord(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(Text, index=True)
    camera_id: Mapped[str] = mapped_column(Text)
    visitor_id: Mapped[str] = mapped_column(Text, index=True)
    event_type: Mapped[str] = mapped_column(Text, index=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    zone_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    ingested_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PosTransaction(Base):
    __tablename__ = "pos_transactions"

    transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    store_id: Mapped[str] = mapped_column(Text, index=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    basket_value_inr: Mapped[object] = mapped_column(Numeric(10, 2))


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_pos_transactions()


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def seed_pos_transactions() -> None:
    path = Path("data/pos_transactions_normalized.csv")
    if not path.exists():
        return
    async with SessionLocal() as session:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                stmt = (
                    insert(PosTransaction)
                    .values(
                        transaction_id=row["transaction_id"],
                        store_id=row["store_id"],
                        timestamp=parse_utc(row["timestamp"]),
                        basket_value_inr=row["basket_value_inr"],
                    )
                    .on_conflict_do_nothing(index_elements=[PosTransaction.transaction_id])
                )
                await session.execute(stmt)
        await session.commit()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
