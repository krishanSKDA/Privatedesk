from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from synthetic.company import COMPANY

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


SUPPLIERS = {
    "bluestone": {
        "vendor_id": "56",
        "name": "Bluestone Freight Services Ltd",
        "address": ["Warehouse Road 7, Jebel Ali Free Zone", "Dubai, United Arab Emirates"],
        "tax_id": "TRN 100 4417 2290 0003",
        "email": "billing@bluestonefreight.example",
        "phone": "+971 4 555 0177",
        "bank": "Gulfstream Commercial Bank, account SYNTHETIC-0056-2201",
        "currency": "USD",
        "terms": "Net 30",
        "expense_account": ("81", "Freight and Delivery"),
    },
    "halcyon": {
        "vendor_id": "57",
        "name": "Halcyon Packaging GmbH",
        "address": ["Hafenstrasse 88", "20457 Hamburg, Germany"],
        "tax_id": "USt-IdNr. DE 000 000 451",
        "email": "accounts@halcyon-packaging.example",
        "phone": "+49 40 5550 1100",
        "bank": "Hanseatic Trade Bank, account SYNTHETIC-0057-8830",
        "currency": "EUR",
        "terms": "Net 45",
        "expense_account": ("52", "Cost of Goods Sold:Packaging"),
    },
    "coastal": {
        "vendor_id": "58",
        "name": "Coastal Office Supplies LLC",
        "address": ["Shop 3, Al Wasl Road", "Dubai, United Arab Emirates"],
        "tax_id": "TRN 100 5521 8830 0003",
        "email": "sales@coastaloffice.example",
        "phone": "+971 4 555 0190",
        "bank": "Gulfstream Commercial Bank, account SYNTHETIC-0058-1145",
        "currency": "USD",
        "terms": "Net 30",
        "expense_account": ("64", "Office Supplies"),
    },
    "cedar": {
        "vendor_id": "59",
        "name": "Cedar Print & Label Co.",
        "address": ["Industrial Area 4, Plot 22", "Sharjah, United Arab Emirates"],
        "tax_id": "TRN 100 6630 4471 0003",
        "email": "orders@cedarprint.example",
        "phone": "+971 6 555 0133",
        "bank": "Emirates Coastal Bank, account SYNTHETIC-0059-7702",
        "currency": "USD",
        "terms": "Net 30",
        "expense_account": ("52", "Cost of Goods Sold:Packaging"),
    },
    "apex": {
        "vendor_id": "60",
        "name": "Apex IT Solutions Pte Ltd",
        "address": ["71 Ayer Rajah Crescent #05-12", "S'pore 139951"],
        "tax_id": "GST Reg. M9-0000451-K",
        "email": "accounts@apexits.example",
        "phone": "+65 5550 4417",
        "bank": "on request",
        "currency": "SGD",
        "terms": "Net 14",
        "expense_account": ("67", "IT Services"),
    },
    "greenline": {
        "vendor_id": "61",
        "name": "Greenline Facilities Management LLC",
        "address": ["Floor 3, Al Khail Gate", "Al Khail Road, Dubai, United Arab Emirates"],
        "tax_id": "TRN 100 7781 3306 0003",
        "email": "invoices@greenlinefm.example",
        "phone": "+971 4 555 0168",
        "bank": "Gulfstream Commercial Bank, account SYNTHETIC-0061-3390",
        "currency": "USD",
        "terms": "Net 15",
        "expense_account": ("71", "Repairs and Maintenance"),
    },
}

INVOICES = [
    {
        "id": "halcyon_hp55102",
        "filename": "halcyon_invoice_sep2026.pdf",
        "split": "build",
        "layout": "european",
        "scanned": False,
        "supplier": "halcyon",
        "invoice_number": "HP-55102",
        "invoice_date": date(2026, 9, 12),
        "due_date": date(2026, 10, 27),
        "date_format": "%d.%m.%Y",
        "currency": "EUR",
        "reference": "PO-2026-0855",
        "lines": [
            ("HP-CB-500 5-ply corrugated box, medium", 5000, "1.84"),
            ("HP-SF-23 Stretch film, 23 micron, 500 mm x 300 m", 120, "18.90"),
            ("HP-BW-12 Bubble wrap, 1.2 m x 100 m", 30, "34.50"),
        ],
        "discount_pct": 5,
        "discount_label": "Mengenrabatt / Volume discount 5%",
        "charges": [("Fracht und Versicherung bis Jebel Ali / Freight and insurance to Jebel Ali", "552.65")],
        "tax_rate": 0,
        "tax_label": "USt. 0% / VAT 0% (export delivery)",
        "notes": [
            "Steuerfreie Ausfuhrlieferung / VAT-exempt export delivery.",
            "Zahlbar innerhalb von 45 Tagen / Payable within 45 days.",
        ],
        "expected_review": [],
    },
    {
        "id": "coastal_cos88217",
        "filename": "coastal_office_invoice.pdf",
        "split": "build",
        "layout": "classic",
        "scanned": False,
        "supplier": "coastal",
        "invoice_number": "COS-88217",
        "invoice_date": date(2026, 9, 18),
        "due_date": date(2026, 10, 18),
        "date_format": "%d/%m/%Y",
        "currency": "USD",
        "reference": "PO-2026-0862",
        "lines": [
            ("A4 copy paper 80gsm, box of 5 reams", 20, "24.50"),
            ("Toner cartridge TN-760, black", 2, "58.00"),
            ("Sticky notes 76 x 76 mm, pack of 12", 10, "2.20"),
        ],
        "tax_rate": 5,
        "tax_label": "VAT 5%",
        "notes": ["Payment terms: 30 days from invoice date.", "Thank you for your business."],
        "expected_review": [],
    },
    {
        "id": "cedar_cpl3391",
        "filename": "scan_20260923_cedar.pdf",
        "split": "build",
        "layout": "classic",
        "scanned": True,
        "supplier": "cedar",
        "invoice_number": "CPL-3391",
        "invoice_date": date(2026, 9, 22),
        "due_date": date(2026, 10, 22),
        "date_format": "%d %b %Y",
        "currency": "USD",
        "reference": "PO-2026-0871",
        "lines": [
            ("Thermal shipping labels 100 x 150 mm", 50000, "0.032"),
            ("Printed carton sleeves, 2-colour", 2000, "0.35"),
            ("Printing plate setup", 1, "15.00"),
        ],
        "tax_rate": 5,
        "tax_label": "VAT 5%",
        "stamp": "RECEIVED 23 SEP 2026",
        "notes": ["Payment terms: 30 days from invoice date.", "Goods delivered 20 Sep 2026 against PO-2026-0871."],
        "expected_review": [],
    },
    {
        "id": "apex_milestone2",
        "filename": "apex_invoice.pdf",
        "split": "build",
        "layout": "messy",
        "scanned": False,
        "supplier": "apex",
        "invoice_number": None,
        "invoice_date": date(2026, 10, 4),
        "due_date": date(2026, 10, 18),
        "date_format": "%d/%m/%y",
        "currency": "SGD",
        "currency_symbol": "$",
        "reference": "ERP Milestone 2 per SOW-2026-03",
        "lines": [
            ("Consulting - data migration and UAT support", 56, "175.00"),
            ("Travel - Dubai site visits x2", 1, "1180.00"),
        ],
        "tax_rate": 9,
        "tax_label": "GST 9%",
        "notes": [],
        "expected_review": ["invoice_number", "invoice_date", "currency"],
    },
    {
        "id": "bluestone_bfs0418",
        "filename": "bluestone_invoice_sep2026.pdf",
        "split": "holdout",
        "layout": "classic",
        "scanned": False,
        "supplier": "bluestone",
        "invoice_number": "BFS-2026-0418",
        "invoice_date": date(2026, 9, 5),
        "due_date": date(2026, 10, 5),
        "date_format": "%d %B %Y",
        "currency": "USD",
        "reference": "Agreement MT-LSA-2026-014",
        "lines": [
            ("Container haulage 40ft, Jebel Ali Port to Al Quoz warehouse (August 2026)", 5, "1250.00"),
            ("Customs clearance, inbound shipments", 5, "220.00"),
            ("Warehouse handling, August 2026 (monthly flat fee)", 1, "500.00"),
        ],
        "tax_rate": 5,
        "tax_label": "VAT 5%",
        "notes": ["Payment terms: net 30 days from the invoice date.", "Late payments carry interest of 1.5% per month."],
        "expected_review": [],
    },
    {
        "id": "greenline_gfm077",
        "filename": "greenline_invoice_oct2026.pdf",
        "split": "holdout",
        "layout": "tax_invoice",
        "scanned": False,
        "supplier": "greenline",
        "invoice_number": "GFM/10/2026/077",
        "invoice_date": date(2026, 10, 1),
        "due_date": date(2026, 10, 16),
        "date_format": "%d-%b-%Y",
        "currency": "USD",
        "reference": "MT-FMA-2026-022",
        "lines": [
            ("Facilities maintenance - monthly fee, October 2026", 1, "4200.00"),
        ],
        "tax_rate": 5,
        "tax_label": "VAT @ 5%",
        "notes": ["Payment due within 15 days of the issue date.", "Service log for September 2026 sent separately."],
        "expected_review": [],
    },
]


def invoice_by_id(invoice_id):
    return next(invoice for invoice in INVOICES if invoice["id"] == invoice_id)


def invoice_totals(invoice):
    subtotal = sum((money(Decimal(quantity) * Decimal(price)) for _, quantity, price in invoice["lines"]), ZERO)
    discount = money(subtotal * Decimal(invoice.get("discount_pct", 0)) / 100)
    charges = sum((money(amount) for _, amount in invoice.get("charges", [])), ZERO)
    net = subtotal - discount + charges
    tax = money(net * Decimal(invoice["tax_rate"]) / 100)
    return {"subtotal": subtotal, "discount": discount, "charges": charges, "net": net, "tax": tax, "total": net + tax}


def total_of(invoice_id):
    return invoice_totals(invoice_by_id(invoice_id))["total"]


BOOKS_NOTICE = (
    "MOCK DATA ONLY. Shaped like QuickBooks Online API responses for a demo. "
    "This is not a real QuickBooks integration and no external system is called."
)

HALCYON_BOOKS_AMOUNT = money("12340.50")


def bill(bill_id, supplier_key, doc_number, txn_date, due_date, amount, paid, memo):
    supplier = SUPPLIERS[supplier_key]
    account_id, account_name = supplier["expense_account"]
    return {
        "Id": bill_id,
        "SyncToken": "0",
        "DocNumber": doc_number,
        "TxnDate": txn_date.isoformat(),
        "DueDate": due_date.isoformat(),
        "VendorRef": {"value": supplier["vendor_id"], "name": supplier["name"]},
        "CurrencyRef": {"value": supplier["currency"]},
        "TotalAmt": float(amount),
        "Balance": 0.0 if paid else float(amount),
        "PrivateNote": memo,
        "Line": [
            {
                "Id": "1",
                "LineNum": 1,
                "Amount": float(amount),
                "Description": memo,
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account_id, "name": account_name}},
            }
        ],
        "MetaData": {"CreateTime": f"{txn_date.isoformat()}T09:00:00+04:00"},
    }


def build_bills():
    return [
        bill("201", "bluestone", "BFS-2026-0352", date(2026, 8, 5), date(2026, 9, 4), money("7420.00"), True, "Freight services July 2026"),
        bill("202", "halcyon", "HP-54977", date(2026, 8, 14), date(2026, 9, 28), money("5890.00"), True, "Packaging order PO-2026-0790"),
        bill("203", "apex", "APX-2026-074", date(2026, 8, 20), date(2026, 9, 3), money("8250.00"), True, "ERP Milestone 1"),
        bill("204", "greenline", "GFM/09/2026/064", date(2026, 9, 1), date(2026, 9, 16), money("4410.00"), True, "Facilities maintenance September 2026"),
        bill("205", "bluestone", "BFS-2026-0418", date(2026, 9, 5), date(2026, 10, 5), total_of("bluestone_bfs0418"), True, "Freight services August 2026"),
        bill("206", "halcyon", "HP-55102", date(2026, 9, 12), date(2026, 10, 27), HALCYON_BOOKS_AMOUNT, False, "Packaging order PO-2026-0855"),
        bill("207", "cedar", "CPL-3391", date(2026, 9, 22), date(2026, 10, 22), total_of("cedar_cpl3391"), True, "Labels and sleeves PO-2026-0871"),
        bill("208", "greenline", "GFM/10/2026/077", date(2026, 10, 1), date(2026, 10, 16), total_of("greenline_gfm077"), False, "Facilities maintenance October 2026"),
        bill("209", "apex", "APX-2026-091", date(2026, 10, 4), date(2026, 10, 18), total_of("apex_milestone2"), False, "ERP Milestone 2"),
    ]


CUSTOMERS = {
    "harborview": {"id": "11", "name": "Harborview Hotels Group", "email": "ap@harborview.example"},
    "pinecrest": {"id": "12", "name": "Pinecrest Foods LLC", "email": "payables@pinecrest.example"},
}


def customer_invoice(invoice_id, customer_key, doc_number, txn_date, due_date, amount, paid, memo):
    customer = CUSTOMERS[customer_key]
    return {
        "Id": invoice_id,
        "SyncToken": "0",
        "DocNumber": doc_number,
        "TxnDate": txn_date.isoformat(),
        "DueDate": due_date.isoformat(),
        "CustomerRef": {"value": customer["id"], "name": customer["name"]},
        "CurrencyRef": {"value": "USD"},
        "TotalAmt": float(amount),
        "Balance": 0.0 if paid else float(amount),
        "Line": [
            {
                "Id": "1",
                "LineNum": 1,
                "Amount": float(amount),
                "Description": memo,
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": {"ItemRef": {"value": "1", "name": "Packaging supplies"}},
            }
        ],
        "MetaData": {"CreateTime": f"{txn_date.isoformat()}T10:00:00+04:00"},
    }


def build_customer_invoices():
    return [
        customer_invoice("301", "harborview", "1042", date(2026, 8, 6), date(2026, 9, 5), money("18600.00"), True, "Packaging supplies July 2026"),
        customer_invoice("302", "pinecrest", "1047", date(2026, 8, 23), date(2026, 9, 22), money("9240.00"), True, "Food-grade packaging August 2026"),
        customer_invoice("303", "pinecrest", "1053", date(2026, 9, 10), date(2026, 10, 10), money("6780.00"), True, "Food-grade packaging September 2026"),
        customer_invoice("304", "harborview", "1051", date(2026, 9, 30), date(2026, 10, 30), money("14320.00"), False, "Packaging supplies September 2026"),
    ]


def build_vendors(bills):
    vendors = []
    for supplier in SUPPLIERS.values():
        open_balance = sum(
            (money(item["Balance"]) for item in bills if item["VendorRef"]["value"] == supplier["vendor_id"]),
            ZERO,
        )
        vendors.append(
            {
                "Id": supplier["vendor_id"],
                "SyncToken": "0",
                "DisplayName": supplier["name"],
                "CompanyName": supplier["name"],
                "PrimaryEmailAddr": {"Address": supplier["email"]},
                "PrimaryPhone": {"FreeFormNumber": supplier["phone"]},
                "BillAddr": {"Line1": supplier["address"][0], "City": supplier["address"][-1]},
                "TaxIdentifier": supplier["tax_id"],
                "CurrencyRef": {"value": supplier["currency"]},
                "TermRef": {"name": supplier["terms"]},
                "Active": True,
                "Balance": float(open_balance),
            }
        )
    return vendors


def build_books():
    bills = build_bills()
    return {
        "_mock": {
            "notice": BOOKS_NOTICE,
            "company": COMPANY["name"],
            "home_currency": "USD",
            "as_of": "2026-10-15",
        },
        "Vendor": build_vendors(bills),
        "Bill": bills,
        "Customer": [
            {"Id": customer["id"], "DisplayName": customer["name"], "PrimaryEmailAddr": {"Address": customer["email"]}, "Active": True}
            for customer in CUSTOMERS.values()
        ],
        "Invoice": build_customer_invoices(),
    }


BANK_ACCOUNT = {
    "bank": "Gulfstream Commercial Bank (fictional)",
    "account_name": "Meridian Trading LLC Operating Account",
    "account_number": "SYNTHETIC-****4417",
    "currency": "USD",
    "period_start": "2026-09-01",
    "period_end": "2026-10-15",
    "opening_balance": "184250.00",
}


def bank_entries():
    return [
        (date(2026, 9, 2), "OFFICE RENT SEP 2026 PARKSIDE PROPERTIES", "RENT-2609", money("9500.00"), None),
        (date(2026, 9, 3), "TRF BLUESTONE FREIGHT SERVICES BFS-2026-0352", "TRF-260903-01", money("7420.00"), None),
        (date(2026, 9, 5), "RCPT HARBORVIEW HOTELS GROUP INV 1042", "RCV-260905-01", None, money("18600.00")),
        (date(2026, 9, 8), "INTL TRF APEX IT SOLUTIONS APX-2026-074 SGD 8,250.00 @ 0.7760", "FX-260908-01", money(Decimal("8250.00") * Decimal("0.7760")), None),
        (date(2026, 9, 10), "INTL TRF HALCYON PACKAGING HP-54977 EUR 5,890.00 @ 1.1057", "FX-260910-01", money(Decimal("5890.00") * Decimal("1.1057")), None),
        (date(2026, 9, 15), "PAYROLL SEP 2026 WPS", "PAY-2609", money("62300.00"), None),
        (date(2026, 9, 16), "TRF GREENLINE FACILITIES GFM/09/2026/064", "TRF-260916-01", money("4410.00"), None),
        (date(2026, 9, 18), "BANK CHARGES INTL TRANSFERS", "FEE-2609", money("45.00"), None),
        (date(2026, 9, 22), "RCPT PINECREST FOODS LLC INV 1047", "RCV-260922-01", None, money("9240.00")),
        (date(2026, 9, 25), "TRF COASTAL OFFICE SUPPLIES COS-88217", "TRF-260925-01", total_of("coastal_cos88217"), None),
        (date(2026, 9, 29), "TRF CEDAR PRINT AND LABEL CPL-3391", "TRF-260929-01", total_of("cedar_cpl3391"), None),
        (date(2026, 9, 30), "CREDIT INTEREST SEP 2026", "INT-2609", None, money("12.40")),
        (date(2026, 10, 1), "OFFICE RENT OCT 2026 PARKSIDE PROPERTIES", "RENT-2610", money("9500.00"), None),
        (date(2026, 10, 5), "TRF BLUESTONE FREIGHT SERVICES BFS-2026-0418", "TRF-261005-01", total_of("bluestone_bfs0418"), None),
        (date(2026, 10, 7), "CORPORATE CARD SETTLEMENT SEP 2026", "CARD-2609", money("3184.60"), None),
        (date(2026, 10, 10), "RCPT PINECREST FOODS LLC INV 1053", "RCV-261010-01", None, money("6780.00")),
        (date(2026, 10, 15), "PAYROLL OCT 2026 WPS", "PAY-2610", money("62300.00"), None),
    ]


def bank_statement_rows():
    balance = money(BANK_ACCOUNT["opening_balance"])
    rows = []
    for entry_date, description, reference, debit, credit in bank_entries():
        balance = balance - (debit or ZERO) + (credit or ZERO)
        rows.append(
            {
                "date": entry_date.isoformat(),
                "description": description,
                "reference": reference,
                "debit_usd": f"{debit:.2f}" if debit else "",
                "credit_usd": f"{credit:.2f}" if credit else "",
                "balance_usd": f"{balance:.2f}",
            }
        )
    return rows


def invoice_truth():
    records = []
    for invoice in INVOICES:
        supplier = SUPPLIERS[invoice["supplier"]]
        review = invoice["expected_review"]
        expected = {
            "supplier": supplier["name"],
            "invoice_date": invoice["invoice_date"].isoformat(),
            "invoice_number": invoice["invoice_number"],
            "amount": f"{invoice_totals(invoice)['total']:.2f}",
            "currency": invoice["currency"],
        }
        for field in review:
            expected[field] = None
        record = {
            "file": invoice["filename"],
            "split": invoice["split"],
            "scanned": invoice["scanned"],
            "layout": invoice["layout"],
            "expected": expected,
            "expected_review": review,
        }
        if review:
            record["reference_values_from_books"] = {
                "invoice_date": invoice["invoice_date"].isoformat(),
                "currency": invoice["currency"],
                "trap": "The page mentions previous invoice APX-2026-074; it is not this invoice's number.",
            }
        records.append(record)
    return records


def expected_discrepancies():
    return [
        {
            "file": "halcyon_invoice_sep2026.pdf",
            "invoice_number": "HP-55102",
            "status": "amount_mismatch",
            "invoice_amount": f"{total_of('halcyon_hp55102'):.2f}",
            "books_amount": f"{HALCYON_BOOKS_AMOUNT:.2f}",
            "difference": f"{total_of('halcyon_hp55102') - HALCYON_BOOKS_AMOUNT:.2f}",
            "currency": "EUR",
        },
        {
            "file": "coastal_office_invoice.pdf",
            "invoice_number": "COS-88217",
            "status": "missing_from_books",
            "note": "Paid on 2026-09-25 per the bank statement, but no bill exists in the books.",
        },
        {
            "file": "scan_20260923_cedar.pdf",
            "invoice_number": "CPL-3391",
            "status": "match",
        },
        {
            "file": "apex_invoice.pdf",
            "invoice_number": None,
            "status": "needs_review",
            "note": "No invoice number on the document. Candidate bill APX-2026-091 matches on vendor and amount only.",
        },
        {
            "file": "bluestone_invoice_sep2026.pdf",
            "invoice_number": "BFS-2026-0418",
            "status": "match",
        },
        {
            "file": "greenline_invoice_oct2026.pdf",
            "invoice_number": "GFM/10/2026/077",
            "status": "match",
        },
    ]
