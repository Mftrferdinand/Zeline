# Spreadsheet Tracker Template

> Build polished multi-sheet Excel/Google-Sheets tracker templates (financial trackers, budgets, dashboards) programmatically with openpyxl — KPI cards, charts, SUMIFS/INDEX-MATCH formula engine, data-validation dropdowns, conditional formatting, and landscape print/tablet layout.

Use when the user asks for a "financial tracker / budget / dashboard template for spreadsheets" or sends an `.xlsx` and says "buat lebih bagus / lengkap / rapih / presisi". Don't hand them a link to someone else's template and don't just describe one — **build the actual `.xlsx` with openpyxl and verify it opens**, then deliver via `MEDIA:/abs/path.xlsx`. The user opens these in Google Sheets AND Excel on a tablet, so formulas must be cross-compatible and the layout must survive landscape.

## Setup

```bash
python3 -c "import openpyxl; print(openpyxl.__version__)" || pip install openpyxl
```
Write the build as a standalone `.py` file and run it with `terminal` (python), not inside `execute_code` — the sandbox may not have openpyxl on its path even when the system python does. Re-run the same script to iterate; each "buat lebih bagus" request = extend the script and re-run.

## Structure that reads as "complete"

A tracker that feels premium has these linked sheets (order matters — Dashboard first):

1. **Dashboard** — KPI cards (Income, Expense, Net, Savings-rate %, Net Worth), a pie/doughnut of spend-by-category, a monthly-cashflow table + bar chart. Everything is `=SUMIFS(...)` over the Transactions sheet, so it auto-updates.
2. **Transactions** — the data-entry engine: Date, Description, Account, Type, Category, Amount, Month(auto `=IFERROR(MONTH(A2),"")`), Year(auto). Pre-format ~500 empty rows + dropdowns.
3. **Budget** — Budget vs Actual per category, Status column (`OK`/`WATCH`>80%/`OVER`) with conditional-format colors.
4. **Savings Goals**, **Recurring Bills**, **Accounts** (opening balance + net movement → current balance), **Lists** (dropdown sources), **Guide**.

## Key openpyxl techniques

- **Dropdowns** = `DataValidation(type="list", formula1="=Lists!$A$2:$A$4")`, then `ws.add_data_validation(dv); dv.add("D2:D504")`. Point at a Lists sheet so the user can extend categories without editing validation.
- **Auto Month/Year columns** let the Dashboard filter by month with a single extra SUMIFS criterion — cheaper than parsing dates in every formula.
- **Charts**: `PieChart`/`DoughnutChart`/`BarChart` + `Reference(ws,min_col,min_row,max_row)`; `titles_from_data=True`; `dataLabels=DataLabelList(); dataLabels.showPercent=True` for the pie.
- **Conditional formatting**: `CellIsRule(operator="equal", formula=['"OVER"'], fill=..., font=...)` for status text; `DataBarRule(start_type="min",end_type="max",color=...)` for in-cell spend bars; `ColorScaleRule` for a used-% gradient.
- **Top-N leaderboard** without a pivot: `=IFERROR(INDEX($B$12:$B$29,MATCH(LARGE($C$12:$C$29,1),$C$12:$C$29,0)),"")`.
- **Styling helpers**: define `fill(hex)`, a `hdr(cell,bg,fg)` header styler, thin `Border`, and a bottom-only border for list rows. Number format `'#,##0'` for currency, `'0.0%'` for ratios, `'dd mmm yyyy'` for dates. `font-variant`/serif isn't available — premium feel comes from a tight palette (navy header, one accent, silver gridlines) + consistent spacing, not fonts.

## Landscape / tablet print (the "presisi" + "landscape di tablet" ask)

Set on EVERY content sheet (skip Lists):
```python
ws.page_setup.orientation = "landscape"
ws.page_setup.paperSize = ws.PAPERSIZE_A4
ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
ws.sheet_view.showGridLines = False
ws.print_title_rows = "1:1"   # repeat header on Transactions
```
`fitToPage=True` + `fitToWidth=1` is what makes it open clean edge-to-edge on a tablet in landscape. Size columns explicitly (a `{col: width}` dict) so nothing clips; freeze the header row (`ws.freeze_panes="A2"`).

## Verify before delivering

```python
wb2 = openpyxl.load_workbook(path)   # reopen — proves it isn't corrupt
print(wb2.sheetnames)
```
Re-order sheets with `wb._sheets.sort(key=lambda s: order.index(s.title) if s.title in order else 99)` and set `wb.active = 0` so Dashboard opens first. Then deliver `MEDIA:<abs path>`.

## Pitfalls

- **`import openpyxl` fails in `execute_code` but works in `terminal`** — the sandbox python differs from system python. Build via a `.py` file run with `terminal`.
- Emoji in a sheet name (e.g. `"📖 Guide"`) is legal but can trip `f"{ws.page_setup.orientation:9}"`-style format strings and some readers — prefer plain names.
- `ws.page_setup.orientation` is `None` until you set it; guard before formatting it in debug prints.
- Google Sheets ignores some Excel-only chart styling but keeps the data + formulas — keep formulas standard (`SUMIFS`, `INDEX/MATCH`, `IFERROR`), avoid Excel-only functions (`XLOOKUP`, dynamic arrays) for cross-compat.
- Don't fabricate a template as prose — the deliverable is a working file the user can import; always run + reopen it.
