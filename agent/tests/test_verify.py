import pytest

from clientops.diff import DuplicateSku, StockRow, diff_stock
from clientops.verify import Mismatch, expected_after_write, verify_rows
from fixtures import load_day


def written(store: list[StockRow], expected: dict[str, int]) -> list[StockRow]:
    return [StockRow(row.sku, row.name, expected[row.sku]) for row in store]


def test_a_correct_write_matches_every_row():
    supplier, store, _ = load_day("kumo", 1)
    expected = expected_after_write(store, diff_stock(supplier, store).changes)
    result = verify_rows(expected, written(store, expected))
    assert result.ok
    assert result.may_save_procedure
    assert result.checked == 42
    assert result.matched == 42
    assert result.mismatches == ()
    assert result.summary == "All 42 rows match."


def test_after_writing_the_store_equals_the_supplier():
    supplier, store, _ = load_day("kumo", 2)
    expected = expected_after_write(store, diff_stock(supplier, store).changes)
    assert expected == {row.sku: row.available for row in supplier}


def test_unchanged_rows_keep_their_value():
    store = [StockRow("A", "Product A", 3), StockRow("B", "Product B", 8)]
    supplier = [StockRow("A", "Product A", 5), StockRow("B", "Product B", 8)]
    assert expected_after_write(store, diff_stock(supplier, store).changes) == {"A": 5, "B": 8}


def test_a_change_for_a_product_not_in_the_store_is_refused():
    supplier, store, _ = load_day("kumo", 1)
    changes = diff_stock(supplier, store).changes
    with pytest.raises(ValueError, match="KC-ETH-200"):
        expected_after_write([row for row in store if row.sku != "KC-ETH-200"], changes)


def test_the_decoy_button_doubling_every_row_is_caught():
    supplier, store, _ = load_day("kumo", 1)
    expected = expected_after_write(store, diff_stock(supplier, store).changes)
    doubled = [StockRow(row.sku, row.name, expected[row.sku] * 2) for row in store]
    result = verify_rows(expected, doubled)
    nonzero = sum(1 for value in expected.values() if value != 0)
    assert not result.ok
    assert not result.may_save_procedure
    assert result.checked == 42
    assert result.matched == 42 - nonzero
    assert result.mismatches[0] == Mismatch("KC-ETH-200", "Ethiopia Guji 200g", expected=6, actual=12)
    assert result.summary == f"{nonzero} of 42 rows don't match after saving. I stopped and didn't save the steps."


def test_one_row_left_unwritten_is_named():
    supplier, store, _ = load_day("kumo", 1)
    expected = expected_after_write(store, diff_stock(supplier, store).changes)
    reread = written(store, expected)
    reread[4] = StockRow("KC-COL-200", "Colombia Huila 200g", 0)
    result = verify_rows(expected, reread)
    assert result.mismatches == (Mismatch("KC-COL-200", "Colombia Huila 200g", expected=24, actual=0),)
    assert result.summary == "1 of 42 rows don't match after saving. I stopped and didn't save the steps."


def test_a_row_missing_on_reread_is_a_mismatch():
    expected = {"A": 1, "B": 2}
    result = verify_rows(expected, [StockRow("A", "Product A", 1)])
    assert result.mismatches == (Mismatch("B", None, expected=2, actual=None),)
    assert result.checked == 2
    assert result.matched == 1


def test_an_unexpected_row_on_reread_is_a_mismatch():
    result = verify_rows({"A": 1}, [StockRow("A", "Product A", 1), StockRow("X", "Product X", 4)])
    assert result.mismatches == (Mismatch("X", "Product X", expected=None, actual=4),)
    assert not result.may_save_procedure


def test_a_row_read_twice_is_refused():
    with pytest.raises(DuplicateSku, match="A"):
        verify_rows({"A": 1}, [StockRow("A", "Product A", 1), StockRow("A", "Product A", 1)])


def test_nothing_to_check_is_not_a_success():
    result = verify_rows({}, [])
    assert not result.ok
    assert result.summary == "No rows were read back after saving. I stopped and didn't save the steps."
