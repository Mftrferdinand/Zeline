# Excel Dashboard Builder

> Build polished multi-sheet Excel (.xlsx) dashboards and trackers with openpyxl — financial/budget/expense trackers, KPI cards, cross-sheet SUMIFS auto-update, charts, dropdown data validation, conditional formatting, and landscape fit-to-page layout. Triggers - financial tracker, budget spreadshe...

Build clean, "perfect", client-ready Excel workbooks with openpyxl — the kind where a **Transactions** input sheet feeds an auto-calculating **Dashboard** via SUMIFS, with charts, dropdowns, conditional formatting, and print-ready landscape layout. Sibling skills: `invoice-generator` (invoices), `status-report-generator` (status reports). Use THIS one for trackers/budgets/dashboards.

## Trigger
"financial tracker", "budget spreadsheet", "expense tracker", "dashboard excel", "spreadsheet template", "buat template keuangan/spreadsheet", "tracker keuangan".

## Golden rule: build > link
When the user asks for a "template", **generate the actual .xlsx** rather than hunting for a download link. A tailored generated file beats a random web template every time, and the user can't inspect a link you can't verify. Deliver via `MEDIA:/abs/path/file.xlsx`.

## Workflow
1. Write the builder as a **script file** then run it with `terminal` (`python3 build_tracker.py`). Do NOT rely on `execute_code` — its sandbox often lacks `openpyxl` even when the Termux python has it. `pip install openpyxl` in terminal if missing.
2. Reload-verify after every build: open the saved file with `openpyxl.load_workbook`, print `sheetnames`, spot-check a couple of formulas/page-setups. Catches corruption before you deliver.
3. Deliver `MEDIA:` path + a short bullet summary of sheets/features.

## Iteration pattern

Dashboard requests arrive as escalating rounds ("make it better", "more complete
and tidier", "precise, landscape, more options"). **Escalate each pass, never
restart** — keep the same script file and layer features onto it:
- Pass 1: core sheets + a basic dashboard.
- Pass 2: extra sheets (Savings Goals, Recurring Bills), status logic, a Guide sheet.
- Pass 3: landscape fit-to-page on every sheet, KPI card grid, doughnut/line charts, Top-N leaderboard, zebra striping, coloured input cells.

Keep one stable palette across passes (navy/blue/orange/silver/white reads
professional; heavy green tends to look garish in a finance sheet). If the
requester names a palette, use theirs.

## Core techniques (openpyxl)

### Cross-sheet auto-updating dashboard
The Dashboard reads a Transactions sheet with SUMIFS — never hardcode totals:
```python
'=SUMIFS(Transactions!$F:$F,Transactions!$D:$D,"Expense",Transactions!$E:$E,B12,Transactions!$H:$H,$O$5)'
```
Add hidden helper columns on Transactions: `Month =IFERROR(MONTH(A2),"")` and `Year =IFERROR(YEAR(A2),"")` so monthly/yearly SUMIFS work. A **Year selector cell** on the dashboard (referenced as `$O$5`) filters everything.

### Dropdown data validation from a Lists sheet
Put Types/Categories/Accounts in a `Lists` sheet, point validations at ranges:
```python
dv = DataValidation(type="list", formula1="=Lists!$B$2:$B$24", allow_blank=True)
tx.add_data_validation(dv); dv.add("E2:E604")
```
Pre-format ~500+ empty rows (number formats + Month/Year formula) so the sheet is ready to type into.

### KPI cards
Merge two cells for a colored label row + a merged value row below with a big bold formula. 5-6 cards across the full width read as a dashboard.

### Charts
`PieChart`/`DoughnutChart` (holeSize=55, dataLabels.showPercent=True), `BarChart` (type "col" or "bar"), `LineChart` for trend. Feed with `Reference(ws, min_col, min_row, max_row)`, `titles_from_data=True`, `set_categories(...)`.

### Conditional formatting
- `CellIsRule(operator="equal", formula=['"OVER"'], fill=..., font=...)` for status columns (OK/WATCH/OVER).
- `DataBarRule(start_type="min", end_type="max", color=BLUE)` for in-cell bars on amounts/progress.
- `ColorScaleRule(...)` for red→white→green on Net columns.
- Color the Type column: Income green, Expense red, Transfer blue.

### Top-N leaderboard
`=IFERROR(INDEX($B$12:$B$29,MATCH(LARGE($C$12:$C$29,1),$C$12:$C$29,0)),"")` + `LARGE(...,1)` for the amount.

### Zebra striping + colored input cells
Fill even rows with a light grey; fill user-input cells (Budget, Saved, Opening Balance) with light blue so it's obvious what to type into.

## Landscape / tablet-presisi layout (do this on EVERY sheet)
```python
from openpyxl.worksheet.properties import PageSetupProperties
ws.page_setup.orientation = "landscape"
ws.page_setup.paperSize = ws.PAPERSIZE_A4
ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)  # REQUIRED — fitToWidth alone does nothing without this
ws.sheet_view.showGridLines = False
ws.page_margins.left = ws.page_margins.right = 0.3
```
Also: `ws.freeze_panes="A2"`, `ws.print_title_rows="1:1"` on data sheets, explicit column widths (don't leave default 8.43), and set the Dashboard's content columns to sum to a 16:10-ish width so it fills a landscape tablet screen.

## Compatibility
Formulas (SUMIFS, INDEX/MATCH, LARGE, IFERROR) work in BOTH Excel and Google Sheets. Tell the user: Google Sheets import via File > Import > Upload; charts/formatting survive.

## Pitfalls
- **`execute_code` sandbox lacks openpyxl** even when terminal python has it → always write a script file and run via `terminal`.
- `PageSetupProperties(fitToPage=True)` is mandatory; setting `fitToWidth` without it silently does nothing.
- After `wb.save`, sheet order from `create_sheet` may not match desired order — sort `wb._sheets` by an explicit order list, then set `wb.active=0`.
- `wb.move_sheet` with a bad offset throws or misplaces; prefer the `_sheets.sort(key=...)` pattern.
- Reload-verify formulas print as strings (openpyxl doesn't compute) — that's expected; you're checking they're present/correct, not their values.
- Avoid emoji in sheet TAB names if the user may open in older Excel; fine in cell text.
- Palette for the user: navy #0A2540 / blue #0A84FF / orange #FF9500 / silver / white. Avoid heavy green — the user calls it "norak".
