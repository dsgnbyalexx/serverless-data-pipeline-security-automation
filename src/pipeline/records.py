"""Parse and validate order files. Pure functions: no AWS calls, easy to test."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

REQUIRED_COLUMNS = ("order_id", "customer_email", "amount", "currency", "created_at")
MAX_ROWS = 10_000
MAX_AMOUNT = Decimal("1000000")

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class FileRejected(Exception):
    """The whole file is unusable. Retrying will not help (a permanent error)."""


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_email: str
    amount: Decimal  # Decimal, never float: money must be exact (and DynamoDB requires it)
    currency: str
    created_at: str  # ISO 8601, normalised to UTC


@dataclass(frozen=True)
class RowError:
    line: int  # line number in the file (the header is line 1)
    errors: tuple[str, ...]


def _parse_amount(raw: str, errors: list[str]) -> Decimal | None:
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        errors.append("amount must be a number")
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        errors.append(f"amount must be greater than 0 and at most {MAX_AMOUNT}")
        return None
    if amount.as_tuple().exponent < -2:
        errors.append("amount must have at most 2 decimal places")
        return None
    return amount


def _parse_timestamp(raw: str, errors: list[str]) -> str | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        errors.append("created_at must be an ISO 8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append("created_at must include a timezone, e.g. 2026-10-24T10:00:00Z")
        return None
    return parsed.astimezone(UTC).isoformat()


def validate_row(row: dict[str, str]) -> tuple[Order | None, list[str]]:
    errors: list[str] = []
    order_id = (row.get("order_id") or "").strip()
    email = (row.get("customer_email") or "").strip().lower()
    currency = (row.get("currency") or "").strip().upper()

    if not ORDER_ID_RE.match(order_id):
        errors.append("order_id must be 1-64 letters, digits, '-' or '_'")
    if len(email) > 254 or not EMAIL_RE.match(email):
        errors.append("customer_email must be a valid email address")
    amount = _parse_amount((row.get("amount") or "").strip(), errors)
    if not CURRENCY_RE.match(currency):
        errors.append("currency must be a 3-letter code such as USD")
    created_at = _parse_timestamp((row.get("created_at") or "").strip(), errors)

    if errors:
        return None, errors
    return Order(order_id, email, amount, currency, created_at), []


def parse_orders_csv(raw: bytes) -> tuple[list[Order], list[RowError]]:
    """Return valid orders and per-row errors. Raise FileRejected for unusable files."""
    try:
        text = raw.decode("utf-8-sig")  # -sig strips the byte-order mark Excel adds
    except UnicodeDecodeError as err:
        raise FileRejected("file is not valid UTF-8 text") from err

    reader = csv.DictReader(io.StringIO(text))
    header = [name.strip() for name in (reader.fieldnames or [])]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise FileRejected(f"missing required columns: {', '.join(missing)}")
    reader.fieldnames = header

    orders: list[Order] = []
    row_errors: list[RowError] = []
    seen_ids: set[str] = set()

    for index, row in enumerate(reader):
        if index >= MAX_ROWS:
            raise FileRejected(f"file has more than {MAX_ROWS} rows")
        line = reader.line_num
        order, errors = validate_row(row)
        if order and order.order_id in seen_ids:
            errors = [f"order_id {order.order_id} appears more than once in this file"]
            order = None
        if order:
            seen_ids.add(order.order_id)
            orders.append(order)
        else:
            row_errors.append(RowError(line, tuple(errors)))

    return orders, row_errors
