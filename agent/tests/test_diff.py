import pytest

from clientops.diff import DuplicateSku, StockChange, StockRow, diff_stock
from fixtures import load_day


def rows(*items: tuple[str, int]) -> list[StockRow]:
    return [StockRow(sku, f"Product {sku}", available) for sku, available in items]


@pytest.mark.parametrize(
    ("client", "day", "changes", "sold_out"),
    [("kumo", 1, 12, 3), ("kumo", 2, 4, 0), ("kumo", 3, 7, 1), ("hinata", 1, 5, 1)],
)
def test_demo_days_give_the_catalog_changes(client, day, changes, sold_out):
    supplier, store, expected = load_day(client, day)
    result = diff_stock(supplier, store)
    assert list(result.changes) == expected
    assert len(result.changes) == changes
    assert result.sold_out == sold_out
    assert result.missing_in_store == ()
    assert result.missing_at_supplier == ()


def test_kumo_day_one_includes_the_rows_on_the_canvas():
    supplier, store, _ = load_day("kumo", 1)
    changes = diff_stock(supplier, store).changes
    for row in (
        StockChange("KC-ETH-200", "Ethiopia Guji 200g", 18, 6),
        StockChange("KC-COL-200", "Colombia Huila 200g", 0, 24),
        StockChange("KC-KEN-200", "Kenya Nyeri 200g", 9, 0),
        StockChange("KC-GUA-500", "Guatemala Antigua 500g", 4, 15),
    ):
        assert row in changes
    assert changes[0] == StockChange("KC-ETH-200", "Ethiopia Guji 200g", 18, 6)


def test_identical_tables_have_no_changes():
    table = rows(("A", 1), ("B", 0), ("C", 7))
    result = diff_stock(table, table)
    assert result.changes == ()
    assert result.sold_out == 0
    assert result.is_empty


def test_empty_tables_have_no_changes():
    result = diff_stock([], [])
    assert result.is_empty


def test_every_row_can_change():
    result = diff_stock(rows(("A", 5), ("B", 0), ("C", 2)), rows(("A", 1), ("B", 4), ("C", 3)))
    assert [change.sku for change in result.changes] == ["A", "B", "C"]
    assert result.sold_out == 1
    assert not result.is_empty


def test_changes_follow_the_store_order_and_use_the_store_name():
    supplier = [StockRow("B", "Supplier B", 9), StockRow("A", "Supplier A", 3)]
    store = [StockRow("A", "Store A", 1), StockRow("B", "Store B", 2)]
    assert diff_stock(supplier, store).changes == (
        StockChange("A", "Store A", 1, 3),
        StockChange("B", "Store B", 2, 9),
    )


def test_a_product_only_at_the_supplier_is_reported_and_not_changed():
    result = diff_stock(rows(("A", 1), ("NEW", 5)), rows(("A", 1)))
    assert result.changes == ()
    assert [row.sku for row in result.missing_in_store] == ["NEW"]
    assert not result.is_empty


def test_a_product_only_in_the_store_is_reported_and_left_alone():
    result = diff_stock(rows(("A", 1)), rows(("A", 1), ("OLD", 4)))
    assert result.changes == ()
    assert [row.sku for row in result.missing_at_supplier] == ["OLD"]


def test_a_sku_listed_twice_on_one_side_is_refused():
    with pytest.raises(DuplicateSku, match="A"):
        diff_stock(rows(("A", 1), ("A", 2)), rows(("A", 1)))
    with pytest.raises(DuplicateSku, match="B"):
        diff_stock(rows(("B", 1)), rows(("B", 1), ("B", 3)))


def test_negative_stock_is_refused():
    with pytest.raises(ValueError, match="negative"):
        StockRow("A", "Product A", -1)


def test_each_table_is_read_once():
    supplier, store, expected = load_day("kumo", 1)
    result = diff_stock(iter(supplier), iter(store))
    assert list(result.changes) == expected


def test_a_change_knows_when_it_sells_out():
    assert StockChange("A", "Product A", 9, 0).sells_out
    assert not StockChange("A", "Product A", 0, 9).sells_out
