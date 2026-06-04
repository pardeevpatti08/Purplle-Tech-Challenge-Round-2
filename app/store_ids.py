import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def store_aliases() -> dict[str, str]:
    path = Path("data/store_layout.json")
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    stores = raw.get("stores", [raw])
    aliases: dict[str, str] = {}
    for store in stores:
        store_id = store.get("store_id")
        if not store_id:
            continue
        values = [store_id, store.get("store_code"), *store.get("aliases", [])]
        for value in values:
            if value:
                aliases[str(value).casefold()] = store_id
    return aliases


def canonical_store_id(store_id: str) -> str:
    return store_aliases().get(store_id.casefold(), store_id)
