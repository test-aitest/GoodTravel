from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from clientops.diff import DuplicateSku, StockChange, StockRow, index_by_sku


@dataclass(frozen=True, slots=True)
class Mismatch:
    sku: str
    name: str | None
    expected: int | None
    actual: int | None


@dataclass(frozen=True, slots=True)
class Verification:
    checked: int
    matched: int
    mismatches: tuple[Mismatch, ...]

    @property
    def ok(self) -> bool:
        return self.checked > 0 and not self.mismatches

    @property
    def may_save_procedure(self) -> bool:
        return self.ok

    @property
    def summary(self) -> str:
        if self.ok:
            return f"All {self.checked} rows match."
        if self.checked == 0:
            return "No rows were read back after saving. I stopped and didn't save the steps."
        return f"{len(self.mismatches)} of {self.checked} rows don't match after saving. I stopped and didn't save the steps."


def expected_after_write(store: Iterable[StockRow], changes: Iterable[StockChange]) -> dict[str, int]:
    expected = {sku: row.available for sku, row in index_by_sku(store, "store admin").items()}
    for change in changes:
        if change.sku not in expected:
            raise ValueError(f"{change.sku} is not in the store admin")
        expected[change.sku] = change.supplier
    return expected


def verify_rows(expected: Mapping[str, int], reread: Iterable[StockRow]) -> Verification:
    remaining = dict(expected)
    seen: set[str] = set()
    mismatches: list[Mismatch] = []
    matched = 0
    for row in reread:
        if row.sku in seen:
            raise DuplicateSku(row.sku, "store admin after saving")
        seen.add(row.sku)
        if row.sku not in remaining:
            mismatches.append(Mismatch(row.sku, row.name, expected=None, actual=row.available))
            continue
        wanted = remaining.pop(row.sku)
        if wanted == row.available:
            matched += 1
        else:
            mismatches.append(Mismatch(row.sku, row.name, expected=wanted, actual=row.available))
    mismatches.extend(Mismatch(sku, None, expected=value, actual=None) for sku, value in remaining.items())
    return Verification(checked=len(expected), matched=matched, mismatches=tuple(mismatches))
