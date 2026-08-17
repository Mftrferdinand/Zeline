# Invoice Generator

> Generate professional invoices (Excel XLSX and Markdown output). Vertex42-style format with items, tax, discount, totals, payment terms.

Generate professional service invoices in Excel (.xlsx) or Markdown. Based on the Vertex42 Service Invoice template — clean layout, auto-calculated totals, proper formatting.

## Trigger

When user says: "buatin invoice", "bikin tagihan", "invoice template", "service invoice", or "generate invoice".

## Workflow

1. Ask or infer: company name, client name, items with quantities/prices, tax rate, invoice number, date
2. Use `scripts/generate_invoice.py` to produce the `.xlsx` file
3. Also output markdown preview so user can see the content immediately
4. Send the `.xlsx` file via MEDIA: path

## Template Format (Markdown)

```
**INVOICE #[NUMBER]**
**Date: DD MONTH YYYY**
**Due Date: DD MONTH YYYY**

**From:**
[Name / Company]
[Address]
[Email / Phone]

**To:**
[Client Name]
[Client Address]
[Client Email]

**Description of Services / Items:**

# | Item Description          | Qty | Unit Price  | Total
1 | [Item]                    |  1  | $X,XXX.XX   | $X,XXX.XX

**Subtotal:**                     $X,XXX.XX
**Tax (XX%):**                    $X,XXX.XX
**Discount:**                     $X,XXX.XX
**Total Due:**                    $X,XXX.XX

**Payment Terms:** Net 15/30/60
**Payment Method:** Bank Transfer / Crypto
```

## Excel Output

The `scripts/generate_invoice.py` script produces a styled `.xlsx` file matching the Vertex42 template:

- Header: Company name (left) + INVOICE (right)
- Info block: Address, Phone, Fax, Website with DATE, INVOICE #, CUSTOMER ID on the right
- BILL TO block with client details
- Items table with alternating row shading
- SUBTOTAL (SUM formula, auto-calculated)
- TAX RATE + TAX (formula-based)
- OTHER / SHIPPING / DISCOUNT line
- TOTAL with double-underline and blue highlight
- Make all checks payable to + Contact info
- "Thank You For Your Business!"
- Print-friendly: landscape, fit to page

## Variants

### P2P Crypto Market Invoice (HTML)

Self-contained Python script that generates a clean, professional HTML invoice. Designed for P2P crypto transactions (USDT, BTC, ETH) but adaptable for any product/service. Single-file output with embedded styling, no dependencies beyond Python 3.

**Trigger:** User asks for P2P invoice, crypto invoice, or template for the community/invoice generator.

**Workflow:**
1. Create `gen.py` in a new directory with DATA dict: seller info, buyer info, items (asset + qty + rate), payment info
2. Run `python3 gen.py` to produce `invoice-output.html`
3. Send the HTML file or deploy to permanent URL (Vercel/Netlify/Pages)

**Template repo:** `https://github.com/user/invoice-template` (MIT licensed, public)

**Key differences from Excel variant:**
- HTML output (not .xlsx) — works in any browser
- Optimized for crypto: asset names, qty with decimals, market note
- Badge label: "P2P MARKET" (no trade/trading wording)
- Network fee included in totals
- Mobile responsive layout

**Pitfall:** Avoid words "trade" or "trading" in P2P context — user prefers "P2P Crypto Market" or "P2P Market" instead.

### Service Invoice (Vertex42 classic)
Standard single-section invoice with DESCRIPTION + AMOUNT columns, single SUBTOTAL/TAX/TOTAL block. Use `scripts/generate_invoice.py` (service).

### Consultant Invoice (Vertex42 consultant)
Two-section invoice with HOURLY SERVICES (HOURS/RATE/AMOUNT) + OTHER SERVICES AND CHARGES, separate SUBTOTAL per section, combined TOTAL TAX from both, S&H and DISCOUNT lines. Use `scripts/consultant_invoice.py`.

Ask user which variant they need, or infer from context:
- Hourly consulting / freelancing → Consultant Invoice
- Simple product/service sale → Service Invoice

## Pitfalls

- User's name/company: confirm exact spelling (e.g. use the exact name the user gives). User has corrected this before.
- The Vertex42 template has specific layout (DATE/INVOICE #/CUSTOMER ID on right side) — do NOT left-align these
- Tax rate is percentage in decimal (0.11 = 11%) — the script handles formatting
- Currency symbol is configurable via the `currency` parameter
- **Row-gap issue:** Items section should not leave a huge gap before SUBTOTAL. Template uses fixed row anchors (SUBTOTAL at row 22 for service, row 20 + 29 for consultant). If there are few items, the script pads with blank rows; do NOT insert extra blank rows manually.
- Copy the final `.xlsx` to `~/storage/downloads/` so user can access it from their file manager


---

## Lampiran: `scripts/generate_invoice.py`

```py
#!/usr/bin/env python3
"""
Professional Service Invoice — Vertex42 style.
Usage: python3 generate_invoice.py [output.xlsx]
"""

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from datetime import datetime
import sys


def create_invoice(company="[Company Name]", street="[Street Address]",
                   city_state="[City, ST  ZIP]", phone="[000-000-0000]", fax="[000-000-0000]",
                   website="", invoice_date=None, invoice_num="[123456]", customer_id="[123]",
                   bill_to_name="[Name]", bill_to_company="[Company Name]",
                   bill_to_street="[Street Address]", bill_to_city="[City, ST  ZIP]",
                   bill_to_phone="[Phone]", items=None, tax_rate=0.0,
                   other_label="OTHER", other_amount=0.0,
                   notes="1. Total payment due in 30 days\n2. Please include the invoice number",
                   payment_company="[Your Company]", contact="[Name, Phone, Email]", currency="$"):
    if items is None: items = [("[Service Fee]", 230.00), ("[Labor: 5 hours at $75/hr]", 375.00)]
    if invoice_date is None: invoice_date = datetime.now()

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Invoice"
    for col, w in {'A':5,'B':36,'C':14,'D':16,'E':20,'F':4,'G':22}.items():
        ws.column_dimensions[col].width = w

    DB, LB, LG = "1F4E79", "D6E4F0", "F2F2F2"
    def f(sz=10, b=False, i=False, c=None):
        kw = {'size': sz, 'bold': b, 'italic': i}
        return Font(**kw) if c is None else Font(**kw, color=c)
    def fl(c): return PatternFill(start_color=c, end_color=c, fill_type="solid")
    bm = Border(bottom=Side(style='medium')); bt = Border(top=Side(style='thin'), bottom=Side(style='thin'))
    ba = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    bd = Border(top=Side(style='medium'), bottom=Side(style='double')); bb = Border(bottom=Side(style='hair'))

    ws.merge_cells('A1:D1'); ws['A1'].value = company; ws['A1'].font = f(16,b=True,c=DB)
    ws.merge_cells('E1:G1'); ws['E1'].value = "INVOICE"; ws['E1'].font = f(16,b=True,c=DB)
    ws['E1'].alignment = Alignment(horizontal='right')
    ws.merge_cells('A2:C2'); ws['A2'].value = street; ws['A2'].font = f(10)
    ws.merge_cells('A3:C3'); ws['A3'].value = city_state; ws['A3'].font = f(10)
    ws['D3'].value = "DATE"; ws['D3'].font = f(10,b=True); ws['D3'].alignment = Alignment(horizontal='right')
    ws['E3'].value = invoice_date.strftime('%Y-%m-%d'); ws['E3'].font = f(10)
    ws['E3'].alignment = Alignment(horizontal='right'); ws['E3'].border = bm
    ws.merge_cells('A4:C4'); ws['A4'].value = f"Phone: {phone}"; ws['A4'].font = f(10)
    ws['D4'].value = "INVOICE #"; ws['D4'].font = f(10,b=True); ws['D4'].alignment = Alignment(horizontal='right')
    ws['E4'].value = invoice_num; ws['E4'].font = f(10); ws['E4'].alignment = Alignment(horizontal='right'); ws['E4'].border = bm
    ws.merge_cells('A5:C5'); ws['A5'].value = f"Fax: {fax}"; ws['A5'].font = f(10)
    ws['D5'].value = "CUSTOMER ID"; ws['D5'].font = f(10,b=True); ws['D5'].alignment = Alignment(horizontal='right')
    ws['E5'].value = customer_id; ws['E5'].font = f(10); ws['E5'].alignment = Alignment(horizontal='right'); ws['E5'].border = bm
    ws.merge_cells('A6:C6'); ws['A6'].value = f"Website: {website}" if website else "Website:"; ws['A6'].font = f(10)

    ws.merge_cells('A9:E9'); ws['A9'].value = "  BILL TO:"; ws['A9'].font = f(10,b=True,c=DB)
    ws['A10'].value = bill_to_name; ws['A10'].font = f(10)
    ws['A11'].value = bill_to_company; ws['A11'].font = f(10)
    ws['A12'].value = bill_to_street; ws['A12'].font = f(10)
    ws['A13'].value = bill_to_city; ws['A13'].font = f(10)
    ws['A14'].value = bill_to_phone; ws['A14'].font = f(10)
    ws['G12'].value = "HOW TO SEND AN INVOICE"; ws['G12'].font = f(9,b=True,c="808080")
    ws['G13'].value = "1) Save or Print as PDF"; ws['G13'].font = f(8,c="808080")
    ws['G14'].value = "2) Email the PDF to the client"; ws['G14'].font = f(8,c="808080")

    ws.merge_cells('A16:D16'); ws['A16'].value = "  DESCRIPTION"; ws['A16'].font = f(10,b=True,c="FFFFFF")
    ws['A16'].fill = fl(DB); ws['E16'].value = "AMOUNT"; ws['E16'].font = f(10,b=True,c="FFFFFF")
    ws['E16'].fill = fl(DB); ws['E16'].alignment = Alignment(horizontal='center')
    for c in ['A','B','C','D','E']: ws[f'{c}16'].border = ba
    ws.row_dimensions[16].height = 22

    item_start = 17
    for i, (desc, amount) in enumerate(items):
        r = item_start + i
        ws.merge_cells(f'A{r}:D{r}'); ws[f'A{r}'].value = f"  {desc}"; ws[f'A{r}'].font = f(10); ws[f'A{r}'].border = bb
        ws[f'E{r}'].value = amount; ws[f'E{r}'].font = f(10)
        ws[f'E{r}'].alignment = Alignment(horizontal='right'); ws[f'E{r}'].number_format = f'{currency}#,##0.00'
        ws[f'E{r}'].border = bb
        if i%2==0:
            ws[f'A{r}'].fill = fl(LG); ws[f'E{r}'].fill = fl(LG)
    item_end = item_start + len(items) - 1
    r = max(item_end + 2, 22)
    while r <= 22:
        ws.merge_cells(f'A{r}:D{r}'); ws[f'A{r}'].border = bb; ws[f'E{r}'].border = bb
        r += 1
    r = 22

    ws[f'D{r}'].value = "SUBTOTAL"; ws[f'D{r}'].font = f(11,b=True)
    ws[f'D{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].value = f"=SUM(E{item_start}:E{item_end})"; ws[f'E{r}'].font = f(11,b=True)
    ws[f'E{r}'].number_format = f'{currency}#,##0.00'; ws[f'E{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].border = bt; ws[f'D{r}'].border = bt; sub_row = r; r += 1

    ws.merge_cells(f'A{r}:C{r}'); ws[f'A{r}'].value = "  COMMENTS / NOTES"; ws[f'A{r}'].font = f(10,b=True,c=DB)
    ws[f'D{r}'].value = "TAX RATE"; ws[f'D{r}'].font = f(10); ws[f'D{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].value = tax_rate; ws[f'E{r}'].font = f(10); ws[f'E{r}'].number_format = '0%'
    ws[f'E{r}'].alignment = Alignment(horizontal='right'); ws[f'E{r}'].border = bm
    ws[f'G{r}'].value = "← Enter tax rate if applicable"; ws[f'G{r}'].font = f(8,c="808080")
    taxr = r; r += 1

    note_lines = notes.split('\n')
    for i, line in enumerate(note_lines):
        ws.merge_cells(f'A{r+i}:C{r+i}')
        ws[f'A{r+i}'].value = f"  {line}"; ws[f'A{r+i}'].font = f(10)
    r += len(note_lines)

    ws[f'D{r}'].value = "TAX"; ws[f'D{r}'].font = f(10); ws[f'D{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].value = f"=E{sub_row}*E{taxr}"; ws[f'E{r}'].font = f(10)
    ws[f'E{r}'].number_format = f'{currency}#,##0.00'; ws[f'E{r}'].alignment = Alignment(horizontal='right')
    ws[f'G{r}'].value = "← Auto-calculated"; ws[f'G{r}'].font = f(8,c="808080")
    taxval = r; r += 1

    ws[f'D{r}'].value = other_label; ws[f'D{r}'].font = f(10)
    ws[f'D{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].value = other_amount; ws[f'E{r}'].font = f(10)
    ws[f'E{r}'].number_format = f'{currency}#,##0.00'; ws[f'E{r}'].alignment = Alignment(horizontal='right')
    ws[f'G{r}'].value = '← Change label to "Shipping" or "Discount"'; ws[f'G{r}'].font = f(8,c="808080")
    otherr = r; r += 1

    ws[f'D{r}'].value = "TOTAL"; ws[f'D{r}'].font = Font(size=14,b=True,color=DB)
    ws[f'D{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].value = f"=E{sub_row}+E{taxval}+E{otherr}"; ws[f'E{r}'].font = Font(size=14,b=True,color=DB)
    ws[f'E{r}'].number_format = f'{currency}#,##0.00'; ws[f'E{r}'].alignment = Alignment(horizontal='right')
    ws[f'E{r}'].border = bd; ws[f'G{r}'].value = "← Change currency in cell format"; ws[f'G{r}'].font = f(8,c="808080")
    ws[f'D{r}'].fill = fl(LB); ws[f'E{r}'].fill = fl(LB); r += 2

    ws.merge_cells(f'D{r}:E{r}'); ws[f'D{r}'].value = "Make all checks payable to"; ws[f'D{r}'].font = f(10,i=True); r += 1
    ws.merge_cells(f'D{r}:E{r}'); ws[f'D{r}'].value = payment_company; ws[f'D{r}'].font = f(10,b=True); r += 2
    ws.merge_cells(f'A{r}:E{r}'); ws[f'A{r}'].value = "If you have any questions about this invoice, please contact"; ws[f'A{r}'].font = f(10,i=True); r += 1
    ws.merge_cells(f'A{r}:E{r}'); ws[f'A{r}'].value = contact; ws[f'A{r}'].font = f(10,b=True); r += 2
    ws.merge_cells(f'A{r}:E{r}'); ws[f'A{r}'].value = "Thank You For Your Business!"; ws[f'A{r}'].font = f(12,b=True,c=DB)

    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = 'landscape'; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 1
    ws.page_margins.top = 0.5; ws.page_margins.bottom = 0.5; ws.page_margins.left = 0.3; ws.page_margins.right = 0.3
    return wb

if __name__ == "__main__":
    wb = create_invoice(company="the user", street="Jl. Contoh No. 123",
        city_state="Yogyakarta, DIY 55111", phone="+62-812-3456-7890", fax="-",
        website="user.com", invoice_num="INV-2026-001", customer_id="CID-001",
        bill_to_name="John Doe", bill_to_company="PT Contoh Jaya", bill_to_street="Jl. Bisnis No. 45",
        bill_to_city="Jakarta, DKI 12345", bill_to_phone="+62-21-1234-5678",
        items=[("Service Fee",230.00),("Labor: 5 hours at $75/hr",375.00)],
        tax_rate=0.11, notes="1. Total payment due in 30 days\n2. Please include the invoice number on your check",
        payment_company="the user", contact="the user - +62-812-3456-7890 - user@email.com")
    out = sys.argv[1] if len(sys.argv) > 1 else "Service-Invoice.xlsx"
    wb.save(out); print(f"Saved: {out}")

```

---

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): scripts/consultant_invoice.py.

