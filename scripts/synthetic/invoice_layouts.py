from html import escape

from synthetic.company import COMPANY
from synthetic.finance import SUPPLIERS, invoice_totals
from synthetic.render import BASE_CSS

INVOICE_CSS = BASE_CSS + """
.supplier { font-size: 15pt; font-weight: bold; margin: 0; }
.doc-title { font-size: 18pt; font-weight: bold; text-align: right; }
.plain td { border: none; padding: 1pt 4pt 1pt 0; vertical-align: top; }
.num { text-align: right; }
.total td { font-weight: bold; font-size: 11pt; }
.stamp { color: #a01010; font-weight: bold; font-size: 13pt; }
.scrawl { font-family: serif; font-style: italic; color: #2040a0; font-size: 12pt; }
.small { font-size: 8.5pt; }
"""

LABELS = {
    "classic": {
        "style": "en",
        "title": "INVOICE",
        "number": "Invoice No.",
        "date": "Invoice Date",
        "due": "Due Date",
        "reference": "PO / Reference",
        "bill_to": "Bill To",
        "description": "Description",
        "quantity": "Qty",
        "unit_price": "Unit Price",
        "amount": "Amount",
        "subtotal": "Subtotal",
        "total": "Total Due",
        "total_format": "{currency} {amount}",
        "payment": "Please pay to {bank}. Quote the invoice number with your payment.",
    },
    "european": {
        "style": "eu",
        "title": "RECHNUNG / INVOICE",
        "number": "Rechnungs-Nr. / Invoice no.",
        "date": "Datum / Date",
        "due": "Fällig am / Due",
        "reference": "Ihre Bestellung / Your order",
        "bill_to": "Rechnungsempfänger / Bill to",
        "description": "Artikel / Item",
        "quantity": "Menge / Qty",
        "unit_price": "Einzelpreis / Unit price",
        "amount": "Betrag / Amount",
        "subtotal": "Zwischensumme / Subtotal",
        "total": "Gesamtbetrag / Total",
        "total_format": "{amount} {currency}",
        "payment": "Bankverbindung / Bank details: {bank}.",
    },
    "tax_invoice": {
        "style": "en",
        "title": "TAX INVOICE",
        "number": "Document Ref",
        "date": "Issue Date",
        "due": "Payment Due",
        "reference": "Contract",
        "bill_to": "Customer",
        "description": "Particulars",
        "quantity": "Units",
        "unit_price": "Rate",
        "amount": "Line Total",
        "subtotal": "Taxable Amount",
        "total": "Amount Due",
        "total_format": "{currency} {amount}",
        "payment": "Remit to {bank}.",
    },
}


def localise(text, style):
    if style == "eu":
        return text.replace(",", " ").replace(".", ",").replace(" ", ".")
    return text


def amount_text(value, style):
    return localise(f"{value:,.2f}", style)


def quantity_text(quantity, style):
    return localise(f"{quantity:,}", style)


def unit_price_text(price, style):
    decimals = max(2, len(price.split(".")[1]) if "." in price else 2)
    return localise(f"{float(price):,.{decimals}f}", style)


def bill_to_block():
    lines = [COMPANY["name"], *COMPANY["address"], COMPANY["tax_id"]]
    return "<br>".join(escape(line) for line in lines)


def standard_layout(invoice):
    labels = LABELS[invoice["layout"]]
    style = labels["style"]
    supplier = SUPPLIERS[invoice["supplier"]]
    totals = invoice_totals(invoice)
    currency = invoice["currency"]

    def total_text(value):
        return labels["total_format"].format(amount=amount_text(value, style), currency=currency)

    supplier_block = "<br>".join(escape(line) for line in [*supplier["address"], supplier["tax_id"], supplier["email"], supplier["phone"]])
    meta_rows = [
        (labels["number"], invoice["invoice_number"]),
        (labels["date"], invoice["invoice_date"].strftime(invoice["date_format"])),
        (labels["due"], invoice["due_date"].strftime(invoice["date_format"])),
        (labels["reference"], invoice["reference"]),
    ]
    meta = "<br>".join(f"<b>{escape(label)}:</b> <span class='code'>{escape(value)}</span>" for label, value in meta_rows)
    line_rows = "".join(
        f"<tr><td>{escape(description)}</td><td class='num'>{quantity_text(quantity, style)}</td>"
        f"<td class='num'>{unit_price_text(price, style)}</td>"
        f"<td class='num'>{amount_text(quantity * float(price), style)}</td></tr>"
        for description, quantity, price in invoice["lines"]
    )
    summary = [(labels["subtotal"], amount_text(totals["subtotal"], style))]
    if totals["discount"]:
        summary.append((invoice["discount_label"], "-" + amount_text(totals["discount"], style)))
    for label, amount in invoice.get("charges", []):
        summary.append((label, amount_text(float(amount), style)))
    summary.append((invoice["tax_label"], amount_text(totals["tax"], style)))
    summary_rows = "".join(f"<tr><td>{escape(label)}</td><td class='num'>{value}</td></tr>" for label, value in summary)
    stamp = f"<p class='stamp'>[ {escape(invoice['stamp'])} ]</p>" if invoice.get("stamp") else ""
    notes = "".join(f"<p class='small'>{escape(note)}</p>" for note in invoice["notes"])
    return f"""
<table class="plain"><tr>
<td><p class="supplier">{escape(supplier['name'])}</p><p class="small">{supplier_block}</p></td>
<td><p class="doc-title">{escape(labels['title'])}</p><p class="small">{meta}</p></td>
</tr></table>
{stamp}
<p><b>{escape(labels['bill_to'])}</b><br>{bill_to_block()}</p>
<table>
<tr><th>{escape(labels['description'])}</th><th>{escape(labels['quantity'])}</th><th>{escape(labels['unit_price'])}</th><th>{escape(labels['amount'])}</th></tr>
{line_rows}
</table>
<table class="plain">
{summary_rows}
<tr class="total"><td>{escape(labels['total'])}</td><td class="num">{total_text(totals['total'])}</td></tr>
</table>
<p class="small">{escape(labels['payment'].format(bank=supplier['bank']))}</p>
{notes}
"""


def messy_layout(invoice):
    supplier = SUPPLIERS[invoice["supplier"]]
    totals = invoice_totals(invoice)
    symbol = invoice["currency_symbol"]

    def dollars(value):
        return f"{symbol}{value:,.2f}"

    line_rows = "".join(
        f"<tr><td>{escape(description)}</td><td>{quantity}</td><td>{dollars(float(price))}</td>"
        f"<td>{dollars(quantity * float(price))}</td></tr>"
        for description, quantity, price in invoice["lines"]
    )
    contact = " / ".join(escape(part) for part in [*supplier["address"], supplier["tax_id"], supplier["email"]])
    return f"""
<p class="supplier">APEX IT SOLUTIONS</p>
<p class="small">{contact}</p>
<p><b>INVOICE / STATEMENT</b></p>
<table class="plain">
<tr><td>To:</td><td>Meridian Trading (Dubai) - attn Ken W.</td></tr>
<tr><td>Inv. No.:</td><td>(to follow)</td></tr>
<tr><td>Date:</td><td>{invoice['invoice_date'].strftime(invoice['date_format'])}</td></tr>
<tr><td>Re:</td><td>{escape(invoice['reference'])}</td></tr>
</table>
<p class="small">Previous invoice APX-2026-074 settled with thanks.</p>
<table>
<tr><th>Item</th><th>Hrs/Qty</th><th>Rate</th><th>Amt</th></tr>
{line_rows}
</table>
<table class="plain">
<tr><td>Sub total</td><td class="num">{dollars(totals['subtotal'])}</td></tr>
<tr><td>{escape(invoice['tax_label'])}</td><td class="num">{dollars(totals['tax'])}</td></tr>
<tr><td>Balance b/f</td><td class="num">{symbol}0.00</td></tr>
<tr class="total"><td>AMOUNT PAYABLE</td><td class="num">{dollars(totals['total'])}</td></tr>
</table>
<p>Payment within 14 days pls. Bank details on request.</p>
<p class="scrawl">hrs ok?? - check w/ Ken before paying</p>
"""


def render_invoice_html(invoice):
    if invoice["layout"] == "messy":
        return messy_layout(invoice)
    return standard_layout(invoice)
