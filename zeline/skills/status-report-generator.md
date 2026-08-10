# Status Report Generator

> Generate structured project status reports in Excel (.xlsx), Markdown, and HTML formats with progress bars, blocker tracking, and next steps.

Generate project status reports in three formats: Excel (.xlsx), Markdown (paste to Notion/GitHub), and HTML (browser-ready). Based on standard project management reporting format with sections for accomplishments, in-progress tasks, blockers, next steps, risks, and notes.

## Trigger

When user says: "status report", "project update", "daily report", "weekly status", "buat report progress", or "bikin status project".

## Workflow

1. Ask or infer: project name, period, status (On Track / At Risk / Critical / Complete), overall progress percentage, accomplishments, in-progress tasks (with owner + ETA), blockers, next steps, risks, notes
2. Use `scripts/report_generator.py` to produce all three formats
3. Output markdown preview immediately so user can see the content
4. Send the `.xlsx` and `.md` files via MEDIA: paths

## Status Colors

| Status     | Color    | Hex       |
|------------|----------|-----------|
| On Track   | Green    | `#27AE60` |
| At Risk    | Orange   | `#E67E22` |
| Critical   | Red      | `#E74C3C` |
| Complete   | Blue     | `#2E86C1` |
| On Hold    | Gray     | `#95A5A6` |

The status badge in the Excel sheet uses the corresponding color fill, and the progress bar matches the status color.

## Output Formats

### Excel (.xlsx)
- PROJECT STATUS REPORT title with date
- Project info block: Project name, Period, Prepared by, Status (colored badge)
- Overall progress bar (visual, 20-cell bar, color-matches status)
- Sections with dark blue headers:
  - **Accomplishments** (this period) — bullet list
  - **In Progress** — table with columns: Task, Progress, Owner, ETA
  - **Blockers / Issues**
  - **Next Steps**
  - **Risks & Mitigation**
  - **Notes / Comments**
- Landscape, fit-to-page print setup

### Markdown (.md)
- H1 title, date/status header line
- ## Accomplishments with checkmarks
- ## In Progress with pipe table
- ## Blockers with ⚠️ prefix
- ## Next Steps with checkbox `- [ ]` format
- ## Risks & Mitigation
- ## Notes section

### HTML (.html)
Inline styled card with:
- Status badge (colored pill)
- Progress bar (rounded, colored)
- Sections with inline tables and lists
- Max-width 700px, responsive, professional look

## Example Markdown Output

```
# Project Status Report: StartKey Core Development
**Date:** Jul 02, 2026 | **Period:** Jul 1-7 | **Status:** At Risk | **Progress:** 65%

## Accomplishments ✓
- Completed API authentication module
- Deployed beta to staging environment
- Fixed 12 reported bugs

## In Progress
| Task | Progress | Owner | ETA |
|------|----------|-------|-----|
| Database Migration | 75% | the user | Jul 5 |

## Blockers / Issues
- ⚠️ Third-party API rate limit

## Next Steps
- [ ] Complete database migration
- [ ] Start user acceptance testing
```

## Pitfalls

- Status string must match one of the five enum values exactly (case-sensitive) for color mapping: "On Track", "At Risk", "Critical", "Complete", "On Hold". If user says something else, map it.
- Progress percentage should be an integer 0-100. The progress bar uses 20 cells (5% per cell).
- In-progress items need 4 fields: (task, progress%, owner, ETA). Do NOT output fewer columns.
- Send .xlsx AND .md. The HTML is optional if user only needs spreadsheet + text.
- Copy `.xlsx` to `~/storage/downloads/` so user can access from file manager.


---

## Lampiran: `scripts/report_generator.py`

```py
#!/usr/bin/env python3
"""
Project Status Report Generator — Excel + Markdown + HTML.
Usage: python3 report_generator.py [output_base_name]
"""

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from datetime import datetime
import sys


def create_excel(project_name="Project Name", report_date=None, report_period="This Week",
                 prepared_by="Your Name", status="On Track", overall_progress=50,
                 accomplishments=None, in_progress_items=None, blockers=None,
                 next_steps=None, risks=None, notes=""):
    if in_progress_items is None: in_progress_items = [("Task C", "75%", "John", "This week")]
    if report_date is None: report_date = datetime.now()

    STATUS_COLORS = {"On Track": "27AE60", "At Risk": "E67E22", "Critical": "E74C3C", "Complete": "2E86C1", "On Hold": "95A5A6"}
    sc = STATUS_COLORS.get(status, "27AE60")

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Status Report"
    for col, w in {'A':4,'B':30,'C':18,'D':15,'E':22,'F':4,'G':22,'H':15,'I':16}.items():
        ws.column_dimensions[col].width = w

    DB, LB, LG = "1F4E79", "D6E4F0", "F2F2F2"
    def f(sz=10, b=False, c=None): return Font(size=sz, bold=b, color=c) if c else Font(size=sz, bold=b)
    def fl(c): return PatternFill(start_color=c, end_color=c, fill_type="solid")
    bb, ba = Border(bottom=Side(style='thin')), Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    row = 1; ws.merge_cells('A1:E1')
    ws['A1'].value = "PROJECT STATUS REPORT"; ws['A1'].font = f(16,b=True,c=DB)
    ws.merge_cells('G1:I1')
    ws['G1'].value = report_date.strftime('%B %d, %Y'); ws['G1'].font = f(10)
    ws['G1'].alignment = Alignment(horizontal='right'); row = 3

    for label, value in [("Project:", project_name), ("Period:", report_period),("Prepared by:", prepared_by),("Status:","")]:
        ws[f'B{row}'].value = label; ws[f'B{row}'].font = f(10,b=True)
        if label == "Status:":
            ws.merge_cells(f'C{row}:D{row}'); ws[f'C{row}'].value = status
            ws[f'C{row}'].font = f(10,b=True,c="FFFFFF"); ws[f'C{row}'].fill = fl(sc)
            ws[f'C{row}'].alignment = Alignment(horizontal='center'); ws[f'C{row}'].border = ba
        else:
            ws.merge_cells(f'C{row}:D{row}'); ws[f'C{row}'].value = value; ws[f'C{row}'].font = f(10)
        row += 1
    row += 1
    ws.merge_cells(f'B{row}:I{row}'); ws[f'B{row}'].value = f"Overall Progress: {overall_progress}%"; ws[f'B{row}'].font = f(12,b=True,c=DB)
    row += 1
    for i in range(20):
        cell = ws.cell(row=row, column=3+i)
        cell.fill = fl(sc) if i < int(overall_progress/5) else fl(LG)
        cell.border = Border(left=Side(style='thin',color='999999'), right=Side(style='thin',color='999999'))
    row += 2

    def section(title, items, headers=None):
        nonlocal row
        ws.merge_cells(f'B{row}:I{row}'); ws[f'B{row}'].value = title
        ws[f'B{row}'].font = f(11,b=True,c="FFFFFF"); ws[f'B{row}'].fill = fl(DB); ws[f'B{row}'].border = ba; row += 1
        if headers:
            for i, h in enumerate(headers):
                ws[f'{chr(ord("B")+i)}{row}'].value = h; ws[f'{chr(ord("B")+i)}{row}'].font = f(9,b=True)
                ws[f'{chr(ord("B")+i)}{row}'].fill = fl(LB); ws[f'{chr(ord("B")+i)}{row}'].border = ba
            row += 1
        for i, item in enumerate(items):
            if isinstance(item, tuple):
                for j, val in enumerate(item):
                    c = chr(ord("B")+j)
                    ws[f'{c}{row}'].value = val; ws[f'{c}{row}'].font = f(9); ws[f'{c}{row}'].border = bb
                    if i%2==0: ws[f'{c}{row}'].fill = fl(LG)
            else:
                ws.merge_cells(f'B{row}:I{row}'); ws[f'B{row}'].value = f"  {item}"
                ws[f'B{row}'].font = f(9); ws[f'B{row}'].border = bb
                if i%2==0: ws[f'B{row}'].fill = fl(LG)
            row += 1
        row += 1

    section("  ACCOMPLISHMENTS (This Period)", accomplishments or [])
    section("  IN PROGRESS", in_progress_items, ["Task","Progress","Owner","ETA"])
    section("  BLOCKERS / ISSUES", blockers if blockers else ["No blockers at this time."])
    section("  NEXT STEPS", next_steps or [])
    if risks: section("  RISKS & MITIGATION", risks)
    if notes: section("  NOTES / COMMENTS", [notes])

    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = 'landscape'; ws.page_setup.fitToWidth = 1
    return wb

def generate_markdown(project_name="Project", report_date=None, report_period="", prepared_by="",
                      status="", overall_progress=0, accomplishments=None, in_progress_items=None,
                      blockers=None, next_steps=None, risks=None, notes=""):
    d = datetime.now() if report_date is None else report_date
    lines = [f"# Project Status Report: {project_name}",
             f"**Date:** {d.strftime('%B %d, %Y')} | **Period:** {report_period} | **By:** {prepared_by}",
             f"**Status:** {status} | **Progress:** {overall_progress}%",""]
    if accomplishments: lines += ["## Accomplishments \u2713"] + [f"- {a}" for a in accomplishments] + [""]
    if in_progress_items:
        lines += ["## In Progress","| Task | Progress | Owner | ETA |","|------|----------|-------|-----|"]
        for t,p,o,e in in_progress_items: lines.append(f"| {t} | {p} | {o} | {e} |")
        lines.append("")
    if blockers: lines += ["## Blockers / Issues"] + [f"- \u26a0\ufe0f {b}" for b in blockers] + [""]
    if next_steps: lines += ["## Next Steps"] + [f"- [ ] {n}" for n in next_steps] + [""]
    if risks: lines += ["## Risks & Mitigation"] + [f"- {r}" for r in risks] + [""]
    if notes: lines.append(f"## Notes\n{notes}")
    return "\n".join(lines)

def generate_html(project_name="Project", status="On Track", overall_progress=50,
                  accomplishments=None, in_progress_items=None, blockers=None, next_steps=None):
    color = {"On Track":"#27AE60","At Risk":"#E67E22","Critical":"#E74C3C"}.get(status,"#27AE60")
    html = f'''<div style="font-family:Arial;max-width:700px;margin:auto;border:1px solid #ddd;padding:24px;border-radius:8px;">
  <h2 style="margin:0;">{project_name}</h2>
  <span style="display:inline-block;background:{color};color:white;padding:4px 14px;border-radius:12px;font-size:12px;margin:8px 0;">{status}</span>
  <div style="margin:16px 0;"><div style="background:#ecf0f1;height:20px;border-radius:10px;overflow:hidden;">
    <div style="background:{color};width:{overall_progress}%;height:20px;"></div></div>
    <div style="text-align:right;font-size:12px;color:#666;">{overall_progress}%</div></div>'''
    if accomplishments:
        html += '<div style="margin:16px 0;"><h3 style="color:#27AE60;">Accomplishments</h3><ul>'
        for a in accomplishments: html += f"<li>{a}</li>"
        html += "</ul></div>"
    if in_progress_items:
        html += '<div style="margin:16px 0;"><h3>In Progress</h3><table style="width:100%;border-collapse:collapse;font-size:13px;"><tr style="background:#f5f5f5;"><th style="padding:6px;border:1px solid #ddd;">Task</th><th style="padding:6px;border:1px solid #ddd;">Progress</th><th style="padding:6px;border:1px solid #ddd;">Owner</th><th style="padding:6px;border:1px solid #ddd;">ETA</th></tr>'
        for t,p,o,e in in_progress_items:
            html += f'<tr><td style="padding:6px;border:1px solid #ddd;">{t}</td><td style="padding:6px;border:1px solid #ddd;text-align:center;">{p}</td><td style="padding:6px;border:1px solid #ddd;">{o}</td><td style="padding:6px;border:1px solid #ddd;">{e}</td></tr>'
        html += "</table></div>"
    if blockers:
        html += '<div style="margin:16px 0;"><h3 style="color:#E74C3C;">Blockers</h3><ul>'
        for b in blockers: html += f"<li>{b}</li>"
        html += "</ul></div>"
    if next_steps:
        html += '<div style="margin:16px 0;"><h3>Next Steps</h3><ol>'
        for n in next_steps: html += f"<li>{n}</li>"
        html += "</ol></div>"
    html += "</div>"
    return html

if __name__ == "__main__":
    data = {"project_name":"StartKey Core Development","report_period":"July 1-7, 2026",
            "prepared_by":"the user","status":"At Risk","overall_progress":65,
            "accomplishments":["Completed API authentication module","Deployed beta to staging","Fixed 12 reported bugs"],
            "in_progress_items":[("Database Migration","75%","the user","Jul 5"),("Payment Integration","40%","Tim","Jul 10"),("UI Dashboard v2","20%","Frontend","Jul 15")],
            "blockers":["Third-party API rate limit - waiting for upgrade approval"],
            "next_steps":["Complete database migration","Start user acceptance testing","Draft deployment runbook"],
            "risks":["Third-party dependency could delay launch by 1 week. Mitigation: implement caching layer."],
            "notes":"Client demo scheduled for July 12."}
    base = sys.argv[1] if len(sys.argv) > 1 else "Project-Status-Report"
    wb = create_excel(**data); wb.save(f"{base}.xlsx"); print(f"Excel: {base}.xlsx")
    with open(f"{base}.md","w") as f: f.write(generate_markdown(**data))
    print(f"MD: {base}.md")
    with open(f"{base}.html","w") as f: f.write(generate_html(project_name=data["project_name"],status=data["status"],overall_progress=data["overall_progress"],accomplishments=data["accomplishments"],in_progress_items=data["in_progress_items"],blockers=data["blockers"],next_steps=data["next_steps"]))
    print(f"HTML: {base}.html")

```
