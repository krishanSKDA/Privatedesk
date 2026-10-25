import copy
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import settings
from app.utils.helpers import normalise_supplier
from app.utils.logger import utc_now

MOCK_LABEL = "MOCK QuickBooks data: a read-only local demo, not a real QuickBooks integration."
ENTITIES = {"Vendor", "Bill", "Invoice", "Customer"}

MATCH = "match"
AMOUNT_MISMATCH = "amount_mismatch"
CURRENCY_MISMATCH = "currency_mismatch"
VENDOR_MISMATCH = "vendor_mismatch"
MISSING_FROM_BOOKS = "missing_from_books"
NEEDS_REVIEW = "needs_review"


class AccountingAccessRevoked(RuntimeError):
    pass


class AccountingConnection:
    def __init__(self, path=None):
        self.path = Path(path or settings.accounting_state_path)

    def state(self):
        if not self.path.exists():
            return {"enabled": True, "changed_at": None, "changed_by": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    @property
    def enabled(self):
        return bool(self.state()["enabled"])

    def set_enabled(self, enabled, user):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {"enabled": enabled, "changed_at": utc_now(), "changed_by": user}
        self.path.write_text(json.dumps(state), encoding="utf-8")
        return state

    def revoke(self, user):
        return self.set_enabled(False, user)

    def restore(self, user):
        return self.set_enabled(True, user)


class MockQuickBooks:
    is_mock = True
    label = MOCK_LABEL

    def __init__(self, path=None, connection=None):
        self.path = Path(path or settings.mock_books_path)
        self.connection = connection or AccountingConnection()
        self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _require_access(self):
        if not self.connection.enabled:
            raise AccountingAccessRevoked("Access to the accounting system has been revoked. Restore it to query the books.")

    def query(self, entity):
        if entity not in ENTITIES:
            raise ValueError(f"Unknown entity '{entity}'. Use one of: {', '.join(sorted(ENTITIES))}.")
        self._require_access()
        return copy.deepcopy(self._data.get(entity, []))

    def vendors(self):
        return self.query("Vendor")

    def bills(self):
        return self.query("Bill")

    def invoices(self):
        return self.query("Invoice")

    def vendor_email(self, supplier_name):
        target = normalise_supplier(supplier_name)
        for vendor in self.vendors():
            if normalise_supplier(vendor["DisplayName"]) == target:
                return vendor.get("PrimaryEmailAddr", {}).get("Address")
        return None


@dataclass(frozen=True)
class Discrepancy:
    file: str
    invoice_number: str | None
    status: str
    message: str
    vendor: str | None = None
    invoice_amount: str | None = None
    books_amount: str | None = None
    difference: str | None = None
    currency: str | None = None
    bill_id: str | None = None
    bill_number: str | None = None

    @property
    def is_issue(self):
        return self.status != MATCH


def to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def money(value):
    return f"{value:.2f}" if value is not None else None


def normalise_number(value):
    return re.sub(r"\s+", "", value).upper()


def same_vendor(left, right):
    return bool(left and right) and normalise_supplier(left) == normalise_supplier(right)


def unmatched(result, supplier, amount, currency, bills):
    candidates = [
        bill for bill in bills
        if same_vendor(bill["VendorRef"]["name"], supplier) and amount is not None and to_decimal(bill["TotalAmt"]) == amount
    ]
    message = "The invoice number is missing or uncertain, so the invoice cannot be matched to the books."
    candidate = candidates[0] if len(candidates) == 1 else None
    if candidate:
        message += f" Possible match: bill {candidate['DocNumber']} (same vendor and amount). Confirm before approving."
    return Discrepancy(
        result.file, None, NEEDS_REVIEW, message, vendor=supplier, invoice_amount=money(amount), currency=currency,
        bill_id=candidate["Id"] if candidate else None, bill_number=candidate["DocNumber"] if candidate else None,
    )


def compare_invoice(result, bills):
    number = result.value("invoice_number")
    supplier = result.value("supplier") or result.fields["supplier"].candidate
    amount = to_decimal(result.value("amount"))
    currency = result.value("currency")
    if number is None:
        return unmatched(result, supplier, amount, currency, bills)
    bill = next((bill for bill in bills if normalise_number(bill["DocNumber"]) == normalise_number(number)), None)
    if bill is None:
        return Discrepancy(
            result.file, number, MISSING_FROM_BOOKS, f"Invoice {number} is not in the books.",
            vendor=supplier, invoice_amount=money(amount), currency=currency,
        )
    books_amount = to_decimal(bill["TotalAmt"])
    books_currency = bill["CurrencyRef"]["value"]
    issues = []
    if supplier and not same_vendor(bill["VendorRef"]["name"], supplier):
        issues.append((VENDOR_MISMATCH, f"the books list the vendor as {bill['VendorRef']['name']}"))
    if currency and currency != books_currency:
        issues.append((CURRENCY_MISMATCH, f"the invoice is in {currency} but the books record {books_currency}"))
    if amount is None:
        issues.append((NEEDS_REVIEW, "the invoice amount needs review before it can be compared"))
    elif amount != books_amount:
        issues.append((AMOUNT_MISMATCH, f"the invoice total is {money(amount)} but the books record {money(books_amount)}"))
    status = issues[0][0] if issues else MATCH
    message = f"Invoice {number} matches bill {bill['DocNumber']}." if not issues else f"Invoice {number}: " + "; ".join(text for _, text in issues) + "."
    difference = money(amount - books_amount) if amount is not None and amount != books_amount else None
    return Discrepancy(
        result.file, number, status, message, vendor=supplier, invoice_amount=money(amount),
        books_amount=money(books_amount), difference=difference, currency=currency or books_currency,
        bill_id=bill["Id"], bill_number=bill["DocNumber"],
    )


def compare_invoices(results, books):
    bills = books.bills()
    return [compare_invoice(result, bills) for result in results]
