import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PosTransaction:
    store_id: str
    transaction_id: str
    timestamp: datetime
    basket_value_inr: float


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_pos_transactions(path: str | Path) -> list[PosTransaction]:
    source = Path(path)
    if not source.exists():
        return []

    transactions: list[PosTransaction] = []
    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            transactions.append(
                PosTransaction(
                    store_id=row["store_id"],
                    transaction_id=row["transaction_id"],
                    timestamp=parse_utc(row["timestamp"]),
                    basket_value_inr=float(row["basket_value_inr"]),
                )
            )
    return transactions
