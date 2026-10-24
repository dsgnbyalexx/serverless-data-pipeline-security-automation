"""Unit tests for file parsing and row validation (no AWS involved)."""

from decimal import Decimal

import pytest

from helpers import csv_file
from pipeline import records
from pipeline.records import FileRejected, parse_orders_csv, validate_row

GOOD = {
    "order_id": "ORD-1001",
    "customer_email": "Ada@Example.com",
    "amount": "49.99",
    "currency": "usd",
    "created_at": "2026-10-24T09:30:00Z",
}


def test_valid_row_is_normalised():
    order, errors = validate_row(GOOD)
    assert errors == []
    assert order.customer_email == "ada@example.com"
    assert order.currency == "USD"
    assert order.amount == Decimal("49.99")
    assert order.created_at == "2026-10-24T09:30:00+00:00"


def test_timestamps_are_converted_to_utc():
    order, _ = validate_row({**GOOD, "created_at": "2026-10-24T10:30:00+01:00"})
    assert order.created_at == "2026-10-24T09:30:00+00:00"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("order_id", "", "order_id"),
        ("order_id", "bad id with spaces", "order_id"),
        ("order_id", "x" * 65, "order_id"),
        ("customer_email", "not-an-email", "customer_email"),
        ("amount", "abc", "number"),
        ("amount", "0", "greater than 0"),
        ("amount", "-5", "greater than 0"),
        ("amount", "1000000.01", "at most"),
        ("amount", "NaN", "greater than 0"),
        ("amount", "Infinity", "greater than 0"),
        ("amount", "10.999", "2 decimal places"),
        ("currency", "US", "3-letter"),
        ("currency", "U5D", "3-letter"),
        ("created_at", "yesterday", "ISO 8601"),
        ("created_at", "2026-10-24T09:30:00", "timezone"),
    ],
)
def test_invalid_values_are_rejected(field, value, message):
    order, errors = validate_row({**GOOD, field: value})
    assert order is None
    assert any(message in e for e in errors), errors


def test_all_errors_in_a_row_are_reported_together():
    _, errors = validate_row({**GOOD, "order_id": "", "amount": "-1", "currency": "x"})
    assert len(errors) == 3


def test_file_with_good_and_bad_rows():
    content = csv_file(
        "A1,a@example.com,10.00,USD,2026-10-24T09:00:00Z",
        "A2,not-an-email,10.00,USD,2026-10-24T09:00:00Z",
        "A3,c@example.com,5,EUR,2026-10-24T09:00:00Z",
    )
    orders, row_errors = parse_orders_csv(content.encode())
    assert [o.order_id for o in orders] == ["A1", "A3"]
    assert [e.line for e in row_errors] == [3]


def test_duplicate_order_id_within_a_file_keeps_the_first():
    content = csv_file(
        "A1,a@example.com,10.00,USD,2026-10-24T09:00:00Z",
        "A1,b@example.com,99.00,USD,2026-10-24T09:00:00Z",
    )
    orders, row_errors = parse_orders_csv(content.encode())
    assert len(orders) == 1 and orders[0].customer_email == "a@example.com"
    assert "more than once" in row_errors[0].errors[0]


def test_excel_byte_order_mark_and_extra_columns_are_handled():
    content = "﻿" + csv_file("A1,a@example.com,1.50,GBP,2026-10-24T09:00:00Z")
    content = content.replace("created_at\n", "created_at,notes\n", 1)
    orders, row_errors = parse_orders_csv(content.encode("utf-8"))
    assert len(orders) == 1 and row_errors == []


def test_missing_columns_reject_the_whole_file():
    with pytest.raises(FileRejected, match="missing required columns: amount"):
        parse_orders_csv(b"order_id,customer_email,currency,created_at\n")


def test_non_utf8_file_is_rejected():
    with pytest.raises(FileRejected, match="UTF-8"):
        parse_orders_csv(b"\xff\xfe\x00binary")


def test_too_many_rows_rejects_the_file(monkeypatch):
    monkeypatch.setattr(records, "MAX_ROWS", 2)
    rows = [f"A{i},a@example.com,1.00,USD,2026-10-24T09:00:00Z" for i in range(3)]
    with pytest.raises(FileRejected, match="more than 2 rows"):
        parse_orders_csv(csv_file(*rows).encode())
