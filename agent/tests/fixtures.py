import json
from pathlib import Path

from clientops.diff import StockChange, StockRow

FIXTURES = Path(__file__).parent / "fixtures"


def load_day(client: str, day: int):
    data = json.loads((FIXTURES / f"{client}-day{day}.json").read_text())
    supplier = [StockRow(row["sku"], row["name"], row["available"]) for row in data["supplier"]]
    store = [StockRow(row["sku"], row["name"], row["available"]) for row in data["store"]]
    changes = [StockChange(c["sku"], c["name"], c["store"], c["supplier"]) for c in data["changes"]]
    return supplier, store, changes
