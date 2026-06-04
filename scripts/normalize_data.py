"""
normalize_data.py
-----------------
Normalizes the raw POS transactions CSV provided in data/ into
data/pos_transactions_normalized.csv.

Rules
-----
- Reads the first POS*.csv found in data/.
- Groups line items by (store_id, order_date, order_time) and sums basket values.
- Converts local IST timestamps to UTC.
- Writes one row per transaction to the normalized CSV.
- No store IDs are hardcoded: whatever store_id values are in the source CSV
  are preserved as-is.

Optional: use --store-id-map to rename mismatched store IDs at normalisation time.
Example: python scripts/normalize_data.py --store-id-map STORE_BLR=ST1008

The layout JSON (data/store_layout.json) is NOT touched by this script.
Edit it directly to add / remove stores, cameras, and zones.
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOCAL_TZ = ZoneInfo("Asia/Kolkata")


def find_pos_source() -> Path:
    matches = sorted(DATA_DIR.glob("POS*.csv"))
    if not matches:
        raise FileNotFoundError(
            "No POS*.csv file found in data/. "
            "Drop your raw POS transactions file there and re-run."
        )
    return matches[0]


def normalize_pos(store_id_map: dict[str, str] | None = None) -> None:
    """
    Read the raw POS CSV, group by (store_id, order_date, order_time),
    sum basket values, convert to UTC, and write the normalized file.

    :param store_id_map: optional rename map, e.g. {"STORE_BLR": "ST1008"}.
                         Applied to the store_id column before grouping.
    """
    source = find_pos_source()
    output = DATA_DIR / "pos_transactions_normalized.csv"
    df = pd.read_csv(source)

    if store_id_map:
        df["store_id"] = df["store_id"].replace(store_id_map)

    # Validate required columns
    required = {"store_id", "order_date", "order_time", "total_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Raw POS CSV is missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    rows: list[dict] = []
    counters: dict[str, int] = {}  # per-store transaction index

    grouped = df.groupby(["store_id", "order_date", "order_time"], dropna=False, sort=True)
    for (store_id, order_date, order_time), group in grouped:
        local_dt = datetime.strptime(
            f"{order_date} {order_time}", "%d-%m-%Y %H:%M:%S"
        ).replace(tzinfo=LOCAL_TZ)
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        basket_value = float(group["total_amount"].fillna(0).sum())

        counters[store_id] = counters.get(store_id, 0) + 1
        idx = counters[store_id]

        rows.append(
            {
                "store_id": store_id,
                "transaction_id": f"{store_id}_{idx:05d}",
                "timestamp": utc_dt.isoformat().replace("+00:00", "Z"),
                "basket_value_inr": f"{basket_value:.2f}",
            }
        )

    rows.sort(key=lambda r: (r["store_id"], r["timestamp"]))

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["store_id", "transaction_id", "timestamp", "basket_value_inr"],
        )
        writer.writeheader()
        writer.writerows(rows)

    store_summary = {}
    for r in rows:
        store_summary[r["store_id"]] = store_summary.get(r["store_id"], 0) + 1

    print(f"Normalized POS data from: {source.name}")
    print(f"Output: {output}  ({len(rows)} transactions)")
    for sid, count in sorted(store_summary.items()):
        print(f"  {sid}: {count} transactions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize raw POS CSV for use by the pipeline.")
    parser.add_argument(
        "--store-id-map",
        nargs="*",
        metavar="OLD=NEW",
        help="Rename store IDs: e.g. --store-id-map STORE_BLR=ST1008 OUTLET_2=ST1076",
        default=[],
    )
    args = parser.parse_args()

    store_id_map: dict[str, str] = {}
    for mapping in (args.store_id_map or []):
        if "=" not in mapping:
            parser.error(f"Invalid --store-id-map entry '{mapping}'. Use OLD=NEW format.")
        old, new = mapping.split("=", 1)
        store_id_map[old.strip()] = new.strip()

    normalize_pos(store_id_map or None)


if __name__ == "__main__":
    main()
