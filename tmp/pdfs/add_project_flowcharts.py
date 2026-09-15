from pathlib import Path
from io import BytesIO
import math
import shutil

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path('C:/Users/INK/OPFIncomeEstimator')
TARGET = ROOT / 'output/pdf/CAIXA_NEXT_2026_Marthus_Sera_Formulario_Inscricao.pdf'
WORK = ROOT / 'tmp/pdfs/flowcharts'
WORK.mkdir(parents=True, exist_ok=True)
BACKUP = WORK / 'form_before_flowcharts.pdf'
if not BACKUP.exists():
    shutil.copy2(TARGET, BACKUP)
reader = PdfReader(BACKUP)
assert len(reader.pages) == 7
assert not any(f.get('/FT') == '/Sig' for f in (reader.get_fields() or {}).values())

for name, filename in [('Arial', 'arial.ttf'), ('Arial-Bold', 'arialbd.ttf')]:
    pdfmetrics.registerFont(TTFont(name, 'C:/Windows/Fonts/' + filename))
W, H = A4
NAVY = colors.HexColor('#143047')
TEAL = colors.HexColor('#007E87')
GRAY = colors.HexColor('#536576')
LIGHT = colors.HexColor('#EFF5F8')
BORDER = colors.HexColor('#C5D8DF')
AMBER = colors.HexColor('#FFF3DF')
AMBER_LINE = colors.HexColor('#AD752C')
buf = BytesIO()
c = canvas.Canvas(buf, pagesize=A4)

def text_center(lines, x, top, width, height, size=9.5, bold=False):
    if isinstance(lines, str):
        lines = lines.split('\n')
    font = 'Arial-Bold' if bold else 'Arial'
    assert all(pdfmetrics.stringWidth(t, font, size) <= width - 14 for t in lines), lines
    leading = size * 1.22
    baseline = H - top - height / 2 + (len(lines)-1)*leading/2 - size*.33
    c.setFillColor(NAVY)
    c.setFont(font, size)
    for line in lines:
        c.drawCentredString(x + width/2, baseline, line)
        baseline -= leading

def box(x, top, width, height, label, fill=LIGHT, stroke=BORDER, bold=False, size=9.5):
    c.setStrokeColor(stroke)
    c.setFillColor(fill)
    c.setLineWidth(.8)
    c.roundRect(x, H-top-height, width, height, 6, fill=1, stroke=1)
    text_center(label, x, top, width, height, size, bold)

def line(points, arrow=True, dashed=False):
    c.setStrokeColor(TEAL)
    c.setFillColor(TEAL)
    c.setLineWidth(1)
    c.setDash(3, 2) if dashed else c.setDash()
    p = c.beginPath()
    p.moveTo(points[0][0], H-points[0][1])
    for x, t in points[1:]:
        p.lineTo(x, H-t)
    c.drawPath(p)
    c.setDash()
    if arrow:
        (x0,t0),(x,t)=points[-2:]
        angle=math.atan2(t-t0,x-x0)
        p=c.beginPath()
        p.moveTo(x,H-t)
        for a in (angle+2.65,angle-2.65):
            p.lineTo(x+5*math.cos(a),H-(t+5*math.sin(a)))
        p.close()
        c.drawPath(p, fill=1, stroke=0)

def label(x, top, value):
    c.setFillColor(GRAY)
    c.setFont('Arial-Bold',8)
    c.drawString(x,H-top,value)

def page(title, subtitle, number):
    c.setFillColor(TEAL)
    c.rect(0,H-9,W,9,fill=1,stroke=0)
    c.setFont('Arial-Bold',9)
    c.setFillColor(GRAY)
    c.drawString(44,H-35,'SANDBOX CAIXA NEXT 2026  |  ANEXO: FLUXOGRAMAS DO PROJETO')
    c.setFont('Arial-Bold',23)
    c.setFillColor(NAVY)
    c.drawString(44,H-71,title)
    c.setFont('Arial',9)
    c.setFillColor(GRAY)
    c.drawString(44,H-93,subtitle)
    c.setStrokeColor(colors.HexColor('#D4E0E6'))
    c.line(44,48,W-44,48)
    c.setFont('Arial',7.5)
    c.drawString(44,35,'Minuta de apoio | 14/09/2026 | Anexo aos sete blocos do formulário')
    c.drawRightString(W-44,35,f'Anexo {number:02d} / 02')

mid=W/2
page('A. Fluxo do projeto','Fluxo conceitual com a integração Open Finance já disponível na CAIXA.',1)
nodes=[
    (116,28,'Cliente autoriza o compartilhamento'),
    (155,30,'CAIXA recebe dados via Open Finance'),
    (196,28,'Validar e padronizar dados'),
    (235,28,'Analisar movimentações financeiras'),
    (274,40,'Identificar fontes de renda e separar\ntransferências, empréstimos e estornos'),
    (325,38,'Analisar recorrência, sazonalidade\ne variação dos recebimentos'),
]
for i,(top,height,content) in enumerate(nodes):
    box(mid-175,top,350,height,content)
    if i:
        prev_top,prev_height,_=nodes[i-1]
        line([(mid,prev_top+prev_height),(mid,top)])
left,right=44,W/2+12
bw=W/2-56
lc,rc=left+bw/2,right+bw/2
line([(mid,363),(mid,374),(lc,374),(lc,386)])
line([(mid,374),(rc,374),(rc,386)])
box(left,386,bw,43,'Estimar renda realizada\nno período observado',bold=True)
box(right,386,bw,43,'Estimar renda mensal\nsustentável com IA',bold=True)
line([(lc,429),(lc,440),(mid,440),(mid,451)])
line([(rc,429),(rc,440),(mid,440)],arrow=False)
box(mid-175,451,350,38,'Consolidar estimativas,\nevidências e incerteza',stroke=TEAL,bold=True)
line([(mid,489),(mid,502)])
diamond_top,diamond_h,diamond_w=502,66,240
p=c.beginPath()
p.moveTo(mid,H-diamond_top)
p.lineTo(mid+diamond_w/2,H-(diamond_top+diamond_h/2))
p.lineTo(mid,H-(diamond_top+diamond_h))
p.lineTo(mid-diamond_w/2,H-(diamond_top+diamond_h/2))
p.close()
c.setFillColor(colors.HexColor('#E5F3F1'))
c.setStrokeColor(TEAL)
c.drawPath(p,fill=1,stroke=1)
text_center('Evidências suficientes\npara apoiar a análise?',mid-120,diamond_top,240,diamond_h,9,True)
line([(mid-diamond_w/2,535),(lc,535),(lc,603)])
line([(mid+diamond_w/2,535),(rc,535),(rc,603)])
label(lc+7,587,'Sim')
label(rc+7,587,'Não / inconclusivo')
box(left,603,bw,48,'Apresentar estimativa\nexplicável ao analista',stroke=TEAL)
box(right,603,bw,48,'Sinalizar limitações e solicitar\nrevisão ou informações\ncomplementares',fill=AMBER,stroke=AMBER_LINE,size=9)
line([(lc,651),(lc,665),(mid,665),(mid,677)])
line([(rc,651),(rc,665),(mid,665)],arrow=False)
box(mid-175,677,350,34,'Apoiar análise financeira\nconforme política da CAIXA',bold=True)
line([(mid,711),(mid,724)])
box(mid-175,724,350,34,'Medir qualidade, tempo de análise\ne redução de retrabalho')
c.setFont('Arial',8)
c.setFillColor(GRAY)
c.drawCentredString(mid,H-778,'Apoio à análise; o experimento não altera automaticamente decisões de crédito.')
c.showPage()

page('B. Validação do estimador','Fluxo separado de avaliação, com referência independente e aprendizado controlado.',2)
box(44,124,W-88,44,'Objetivo: medir a qualidade das estimativas, identificar limitações\ne orientar a evolução das regras e dos modelos.',size=10)

box(55,205,230,58,'Dados sintéticos ou\ndados reais autorizados',bold=True)
line([(170,263),(170,296)])
box(55,296,230,54,'Executar estimador\ne registrar estimativas',stroke=TEAL,bold=True)

box(323,292,217,62,'Referência independente\nou verdade do simulador',fill=AMBER,stroke=AMBER_LINE,bold=True,size=9.5)
line([(170,350),(170,379),(mid,379),(mid,416)])
line([(431.5,354),(431.5,379),(mid,379)],arrow=False)
label(65,373,'Estimativas')
label(366,398,'Renda de referência')
box(mid-180,416,360,56,'Comparar estimativas\ncom a renda de referência',stroke=TEAL,bold=True,size=11)
line([(mid,472),(mid,508)])
box(mid-180,508,360,60,'Medir erros, incerteza\ne desempenho por perfil',bold=True,size=11)
line([(mid,568),(mid,604)])
box(mid-180,604,360,56,'Aprimorar e revalidar\nregras e modelos',stroke=TEAL,bold=True,size=11)

box(44,699,W-88,66,'A renda de referência entra apenas na avaliação deste fluxo.\nO estimador recebe somente os dados disponíveis na data da análise.\nRevalidação usa avaliação separada do ajuste de regras e modelos.',size=9)
c.showPage()
c.save()

writer=PdfWriter()
writer.clone_document_from_reader(reader)
annex=PdfReader(BytesIO(buf.getvalue()))
for p in annex.pages:
    writer.add_page(p)
out=WORK/'updated.pdf'
with out.open('wb') as f:
    writer.write(f)
check=PdfReader(out)
assert len(check.pages)==9
fields_before=reader.get_fields() or {}
fields_after=check.get_fields() or {}
assert set(fields_before)==set(fields_after)
for key in fields_before:
    assert fields_before[key].get('/V')==fields_after[key].get('/V'),key
    assert fields_before[key].get('/FT')==fields_after[key].get('/FT'),key
for i in range(7):
    assert reader.pages[i].extract_text()==check.pages[i].extract_text(),i
    assert reader.pages[i].get_contents().get_data()==check.pages[i].get_contents().get_data(),i
    old_annots=reader.pages[i].get('/Annots',[])
    new_annots=check.pages[i].get('/Annots',[])
    assert len(old_annots)==len(new_annots)
    for before,after in zip(old_annots,new_annots):
        b,a=before.get_object(),after.get_object()
        if b.get('/Subtype')=='/Widget':
            assert a.get('/Subtype')=='/Widget'
            assert b.get('/V')==a.get('/V')
            assert '/AP' in a
            canonical=fields_after.get(a.get('/T'))
            if canonical:
                assert a.get('/V')==canonical.get('/V')
shutil.copy2(out,TARGET)
print(f'Updated: {TARGET}\nPages: 9\nPreserved fields: {len(fields_after)}\nOriginal seven page streams and text: unchanged')
