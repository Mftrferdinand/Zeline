# Document Templater

> Generate structured business documents (invoices, receipts, quotes, contracts) as Excel (.xlsx) or markdown — general-purpose templating with Python + openpyxl for AI agents.

Generate structured business documents for download or share. Supports Excel (.xlsx) via openpyxl and markdown format for inline display. Invoices, receipts, quotes, contracts — any table-heavy document.

## Prerequisites

```bash
pip install openpyxl
```

## Invoice Generator (xlsx)

### Python Script

Location: `scripts/generate_invoice.py`

Usage:
```bash
python3 scripts/generate_invoice.py "MyInvoice.xlsx"
```

Edit the script to set items, client info, tax rate, discount, and notes.

### Schema

```
Sheet: Invoice
Columns: A (#), B (Item Description), C (Qty), D (Unit Price), E (Total)

Row 1:  INVOICE #[NUMBER]               merged, blue header bold
Row 2:  Date + Due Date                 merged, centered
Row 4:  FROM / TO                       two-column
Row 5-7: Sender details                 left | Receiver details right
Row 9:  Table headers                   blue fill + white bold (#, Item, Qty, Price, Total)
Row 10+: Item rows
Row N+2: Subtotal
Row N+3: Tax (X%)
Row N+4: Discount
Row N+5: TOTAL DUE                      blue fill bold
Row N+7: Payment Terms
Row N+8: Payment Method
Row N+9: Bank Details
Row N+11: Notes:
Row N+12: Notes text
```

### Customization

Edit the values dict in `scripts/generate_invoice.py`:
- `invoice_num`, `date`, `due_date`
- `from_name`, `from_addr`, `from_email`
- `to_name`, `to_addr`, `to_email`
- `items` — list of (description, quantity, unit_price) tuples
- `tax_rate` (float, e.g. 0.11 for 11%)
- `discount` (float)
- `currency` (default "$")
- `notes`, `payment_terms`, `payment_method`, `bank_details`

## Markdown Output Format

For AI agents that output text (not files), use this template:

**INVOICE #[NUMBER]**
**Date: DD MONTH YYYY**
**Due Date: DD MONTH YYYY**

**From:**
[Name]
[Address]
[Email]

**To:**
[Name]
[Address]
[Email]

```
# | Item Description          | Qty | Unit Price  | Total
1 | [Item]                   |  N  | $X,XXX.XX   | $X,XXX.XX
```

```
Subtotal                     $X,XXX.XX
Tax (X%)                     $X,XXX.XX
Discount                     $X,XXX.XX
Total Due                    $X,XXX.XX
```

**Payment Terms:** [Net 15/30/60]
**Payment Method:** [Bank Transfer / Crypto / PayPal]
**Bank Details:** [optional]

**Notes:**
[Thank you / T&C]

## Trigger Behavior

When user says "buatin invoice", "invoice", "bikin tagihan", "bikin quote", "generate receipt":
1. Ask details: client name, items/prices, invoice number (optional), date
2. Ask format: Excel file or inline markdown
3. For Excel: run `scripts/generate_invoice.py` and deliver
4. For markdown: output the formatted template above


---

## Lampiran: `scripts/generate_invoice.py`

```py
#!/usr/bin/env python3
"""Generate a general invoice Excel file."""

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from datetime import datetime, timedelta
import sys


def create_invoice(
    invoice_num="INV-001",
    date=None,
    due_date=None,
    from_name="Your Name",
    from_addr="Your Address",
    from_email="your@email.com",
    to_name="Client Name",
    to_addr="Client Address",
    to_email="client@email.com",
    items=None,
    tax_rate=0.11,
    discount=0.0,
    currency="$",
    notes="Thank you for your business!",
    payment_terms="Net 15",
    payment_method="Bank Transfer",
    bank_details="-",
):
    if items is None:
        items = [("Service", 1, 100.0)]

    if date is None:
        date = datetime.now()
    if due_date is None:
        due_date = date + timedelta(days=15)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"

    # Styles
    header_font = Font(bold=True, size=16, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    label_font = Font(bold=True, size=11)
    normal_font = Font(size=11)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    total_font = Font(bold=True, size=12)
    total_fill = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")

    # Column widths
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 15

    # === HEADER ===
    ws.merge_cells("A1:E1")
    cell = ws["A1"]
    cell.value = f"INVOICE #{invoice_num}"
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40

    # Date row
    ws.merge_cells("A2:E2")
    ws["A2"].value = f"Date: {date.strftime('%d %B %Y')}  |  Due Date: {due_date.strftime('%d %B %Y')}"
    ws["A2"].font = Font(size=10)
    ws["A2"].alignment = Alignment(horizontal="center")

    # === FROM / TO ===
    ws["A4"].value = "FROM:"
    ws["A4"].font = label_font
    ws["A5"].value = from_name
    ws["A6"].value = from_addr
    ws["A7"].value = from_email

    ws["D4"].value = "TO:"
    ws["D4"].font = label_font
    ws["D5"].value = to_name
    ws["D6"].value = to_addr
    ws["D7"].value = to_email

    # === ITEMS TABLE ===
    row = 9
    table_headers = ["#", "Item Description", "Qty", "Unit Price", "Total"]
    for col, h in enumerate(table_headers, 1):
        cell = ws.cell(row=row, column=col)
        cell.value = h
        cell.font = Font(bold=True, size=11, color="FFFFFF")
        cell.fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    ws.row_dimensions[row].height = 25

    subtotal = 0
    for idx, (desc, qty, price) in enumerate(items, 1):
        row += 1
        total = qty * price
        subtotal += total

        ws.cell(row=row, column=1, value=idx).alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=2, value=desc)
        ws.cell(row=row, column=3, value=qty).alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=4, value=price).number_format = f'{currency}#,##0.00'
        ws.cell(row=row, column=5, value=total).number_format = f'{currency}#,##0.00'

        for col in range(1, 6):
            ws.cell(row=row, column=col).border = thin_border
            ws.cell(row=row, column=col).font = normal_font

    # === TOTALS ===
    row += 2
    tax_amount = subtotal * tax_rate
    grand_total = subtotal + tax_amount - discount

    labels = ["Subtotal", f"Tax ({int(tax_rate * 100)}%)", "Discount", "TOTAL DUE"]
    values = [subtotal, tax_amount, discount, grand_total]

    for lbl, val in zip(labels, values):
        ws.merge_cells(f"A{row}:D{row}")
        ws.cell(row=row, column=1, value=lbl).font = total_font if "TOTAL" in lbl else label_font
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="right")
        ws.cell(row=row, column=1).border = thin_border

        ws.cell(row=row, column=5, value=val).number_format = f'{currency}#,##0.00'
        ws.cell(row=row, column=5).font = total_font if "TOTAL" in lbl else label_font
        ws.cell(row=row, column=5).alignment = Alignment(horizontal="right")
        ws.cell(row=row, column=5).border = thin_border

        if "TOTAL" in lbl:
            for col in range(1, 6):
                ws.cell(row=row, column=col).fill = total_fill
        ws.row_dimensions[row].height = 25
        row += 1

    # === PAYMENT TERMS ===
    row += 1
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value=f"Payment Terms: {payment_terms}").font = label_font

    row += 1
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value=f"Payment Method: {payment_method}").font = label_font

    row += 1
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value=f"Bank Details: {bank_details}").font = label_font

    # === NOTES ===
    row += 2
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value="Notes:").font = label_font
    row += 1
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value=notes).font = normal_font

    # Print setup
    ws.print_area = f"A1:E{row}"
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    return wb


if __name__ == "__main__":
    # Example usage
    items = [
        ("Market Analysis Setup", 1, 500.00),
        ("Bot Development", 2, 750.00),
        ("Monthly Maintenance", 1, 200.00),
    ]

    wb = create_invoice(
        invoice_num="INV-2026-001",
        from_name="Your Company Name",
        from_addr="Yogyakarta, Indonesia",
        from_email="user@email.com",
        to_name="John Doe",
        to_addr="Jakarta, Indonesia",
        to_email="john@email.com",
        items=items,
        tax_rate=0.11,
        notes="Late payment subject to 2% interest per month.",
    )

    output = sys.argv[1] if len(sys.argv) > 1 else "Invoice.xlsx"
    wb.save(output)
    print(f"Invoice saved to: {output}")

```
