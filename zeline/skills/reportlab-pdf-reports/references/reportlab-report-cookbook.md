# reportlab Report Cookbook — copy-paste building blocks

Shared scaffold to put in the MAIN script; part files receive `P, bullet, title,
cmp_table, callout, s` as args (see SKILL.md chunked-builder pattern).

## Imports, fonts, palette

```python
from pathlib import Path
import os, importlib.util
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Spacer, Table, TableStyle, PageBreak, HRFlowable)

FD = Path('/data/data/com.termux/files/usr/share/fonts/TTF')  # Termux DejaVu
pdfmetrics.registerFont(TTFont('DV',  str(FD/'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('DVB', str(FD/'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFont(TTFont('DVO', str(FD/'DejaVuSans-Oblique.ttf')))
pdfmetrics.registerFont(TTFont('DVM', str(FD/'DejaVuSansMono.ttf')))

NAVY=colors.HexColor('#071D49'); BLUE=colors.HexColor('#1D4ED8')
PALE=colors.HexColor('#F5F8FC'); LIGHT=colors.HexColor('#EAF3FF')
LINE=colors.HexColor('#D8E1EC'); INK=colors.HexColor('#182234')
MUTED=colors.HexColor('#5E6B7A'); WHITE=colors.white
GREEN=colors.HexColor('#177A53'); GRNBG=colors.HexColor('#EAF8F2')
AMBER=colors.HexColor('#E39A31'); AMBBG=colors.HexColor('#FFF5E8')
```

## Doc class with running header/footer (NO watermark by default)

```python
class Doc(BaseDocTemplate):
    def __init__(self, f, **kw):
        super().__init__(f, **kw)
        fr = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                   leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='m', frames=fr, onPage=self.deco))
    def deco(self, c, d):
        c.saveState()
        if d.page > 1:                       # keep cover clean
            c.setStrokeColor(LINE)
            c.line(18*mm, A4[1]-14*mm, A4[0]-18*mm, A4[1]-14*mm)
            c.line(18*mm, 14*mm, A4[0]-18*mm, 14*mm)
            c.setFont('DV', 7.5); c.setFillColor(MUTED)
            c.drawString(18*mm, A4[1]-10.5*mm, 'REPORT TITLE • SUBTITLE')
            c.drawRightString(A4[0]-18*mm, 10*mm, f'Page {d.page}')
        c.restoreState()
```

## Styles + helpers

```python
s = getSampleStyleSheet()
def sty(n, **kw): s.add(ParagraphStyle(name=n, **kw))
sty('CK', fontName='DVB', fontSize=10, leading=13, textColor=colors.HexColor('#8EC5FF'), alignment=TA_CENTER, spaceAfter=6)
sty('CT', fontName='DVB', fontSize=27, leading=33, textColor=WHITE, alignment=TA_CENTER, spaceAfter=12)
sty('CS', fontName='DV',  fontSize=11, leading=17, textColor=colors.HexColor('#DCEAFF'), alignment=TA_CENTER)
sty('H1', fontName='DVB', fontSize=19, leading=24, textColor=NAVY, spaceAfter=8)
sty('H2', fontName='DVB', fontSize=13, leading=17, textColor=BLUE, spaceBefore=10, spaceAfter=6)
sty('B',  fontName='DV',  fontSize=9.4, leading=14.5, textColor=INK, spaceAfter=6, alignment=TA_JUSTIFY)
sty('SE', fontName='DV',  fontSize=7.9, leading=11.5, textColor=INK)
sty('SBE',fontName='DVB', fontSize=7.9, leading=11.5, textColor=INK)
sty('CE', fontName='DV',  fontSize=9.5, leading=15, textColor=NAVY, leftIndent=10, rightIndent=10, spaceBefore=4, spaceAfter=4)
sty('BU', fontName='DV',  fontSize=9, leading=13.8, textColor=INK, leftIndent=13, firstLineIndent=-7, bulletIndent=5, spaceAfter=3)
sty('ME', fontName='DVM', fontSize=7.7, leading=11.5, textColor=INK, backColor=PALE, borderColor=LINE, borderWidth=.5, borderPadding=7, spaceBefore=4, spaceAfter=7)
sty('TOC',fontName='DV',  fontSize=10, leading=18, textColor=INK)

P = lambda x, st='B': Paragraph(x, s[st])
def bullet(x): return P('• '+x, 'BU')
def title(n, x, intro=''):
    a = [P(f'{n}. {x}', 'H1'), HRFlowable(width='100%', thickness=1.2, color=BLUE, spaceAfter=8)]
    if intro: a.append(P(intro))
    return a
def cmp_table(rows, headers=('Area','vA','vB'), widths=(39,62,62)):
    d = [[P(f'<b>{h}</b>','SE') for h in headers]]
    d += [[P(a,'SBE'),P(b,'SE'),P(c,'SE')] for a,b,c in rows]
    t = Table(d, colWidths=[w*mm for w in widths], repeatRows=1, hAlign='LEFT')
    cmds = [('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),
            ('GRID',(0,0),(-1,-1),.45,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]
    for r in range(1,len(d)):
        cmds.append(('BACKGROUND',(0,r),(-1,r), WHITE if r%2 else PALE))
    t.setStyle(TableStyle(cmds)); return t
def callout(x, color=LIGHT, border=BLUE):
    return Table([[P(x,'CE')]], colWidths=[174*mm],
        style=TableStyle([('BACKGROUND',(0,0),(-1,-1),color),('BOX',(0,0),(-1,-1),.8,border),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
```

## Cover + meta strip

```python
cover = Table([[[
    Spacer(1,16*mm), P('TECHNICAL COMPARISON REPORT','CK'),
    P('Title<br/>vA → vB','CT'),
    P('One-line subtitle describing the document','CS'),
    Spacer(1,20*mm), P('Org — tagline','CS'), Spacer(1,14*mm),
]]], colWidths=[174*mm], rowHeights=[158*mm])
cover.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
    ('LEFTPADDING',(0,0),(-1,-1),16*mm),('RIGHTPADDING',(0,0),(-1,-1),16*mm)]))
story += [Spacer(1,20*mm), cover, Spacer(1,8*mm), meta, PageBreak()]
```

## "catalog" table (2- or 3-col reference tables, alternating rows)

```python
def catalog(headers, rows, widths):
    d=[[P(f'<b>{h}</b>','SE') for h in headers]]
    d+=[[P(r[0],'SBE')]+[P(x,'SE') for x in r[1:]] for r in rows]
    t=Table(d, colWidths=[w*mm for w in widths], repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('GRID',(0,0),(-1,-1),.45,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,PALE]),
        ('LEFTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    return t
```

## Build + save

```python
doc = Doc(str(OUT), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
          topMargin=20*mm, bottomMargin=18*mm, title=TITLE, author='Org', subject='...')
doc.build(story); print('WROTE', OUT)
```

## Gotchas
- Inline markup in Paragraphs is mini-HTML: `<b>`, `<i>`, `<br/>`, `<font name="DVM">code</font>`. Escape literal `&` as `&amp;` (e.g. "Media &amp; design") or reportlab throws a paraparser error.
- `repeatRows=1` on tables re-draws the header row after a page break — essential for long comparison tables.
- To control density and avoid empty pages: DON'T `PageBreak()` after every section. Add a `Spacer(1, 6*mm)` between sections and let the frame flow; only `PageBreak()` before a genuinely new major part.
- Reportlab wheel + DejaVu fonts both work on Termux/ARM. If a glyph shows as a box, you used a non-DejaVu font name for that run.
