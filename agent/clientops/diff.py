from collections.abc import Iterable
from dataclasses import dataclass


class DuplicateSku(ValueError):
    def __init__(self, sku: str, side: str):
        super().__init__(f"SKU {sku} is listed more than once in the {side}")
        self.sku = sku
        self.side = side


@dataclass(frozen=True, slots=True)
class StockRow:
    sku: str
    name: str
    available: int

    def __post_init__(self):
        if self.available < 0:
            raise ValueError(f"{self.sku} has negative stock: {self.available}")


@dataclass(frozen=True, slots=True)
class StockChange:
    sku: str
    name: str
    store: int
    supplier: int

    @property
    def sells_out(self) -> bool:
        return self.supplier == 0


@dataclass(frozen=True, slots=True)
class StockDiff:
    changes: tuple[StockChange, ...]
    missing_in_store: tuple[StockRow, ...]
    missing_at_supplier: tuple[StockRow, ...]

    @property
    def sold_out(self) -> int:
        return sum(1 for change in self.changes if change.sells_out)

    @property
    def is_empty(self) -> bool:
        return not (self.changes or self.missing_in_store or self.missing_at_supplier)


def index_by_sku(rows: Iterable[StockRow], side: str) -> dict[str, StockRow]:
    indexed: dict[str, StockRow] = {}
    for row in rows:
        if row.sku in indexed:
            raise DuplicateSku(row.sku, side)
        indexed[row.sku] = row
    return indexed


def diff_stock(supplier: Iterable[StockRow], store: Iterable[StockRow]) -> StockDiff:
    unmatched = index_by_sku(supplier, "supplier list")
    seen: set[str] = set()
    changes: list[StockChange] = []
    missing_at_supplier: list[StockRow] = []
    for row in store:
        if row.sku in seen:
            raise DuplicateSku(row.sku, "store admin")
        seen.add(row.sku)
        offered = unmatched.pop(row.sku, None)
        if offered is None:
            missing_at_supplier.append(row)
        elif offered.available != row.available:
            changes.append(StockChange(row.sku, row.name, row.available, offered.available))
    return StockDiff(tuple(changes), tuple(unmatched.values()), tuple(missing_at_supplier))
