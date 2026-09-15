from pathlib import Path
import json
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/pdf/CAIXA_NEXT_2026_Marthus_Sera_Proposta_Inicial.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)
for name, filename in [('Arial', 'arial.ttf'), ('Arial-Bold', 'arialbd.ttf'), ('Arial-Italic', 'ariali.ttf')]:
    pdfmetrics.registerFont(TTFont(name, 'C:/Windows/Fonts/' + filename))
pdfmetrics.registerFontFamily('Arial', normal='Arial', bold='Arial-Bold', italic='Arial-Italic', boldItalic='Arial-Bold')

NAVY = HexColor('#123247')
TEAL = HexColor('#007F86')
INK = HexColor('#223846')
MUTED = HexColor('#536977')
PALE = HexColor('#EFF5F7')
LINE = HexColor('#D7E2E7')
AMBER = HexColor('#A86213')
W = A4[0] - 100

styles = {
    'body': ParagraphStyle('body', fontName='Arial', fontSize=10.1, leading=14.2, textColor=INK, spaceAfter=8),
    'small': ParagraphStyle('small', fontName='Arial', fontSize=8.3, leading=11.3, textColor=MUTED, spaceAfter=6),
    'table': ParagraphStyle('table', fontName='Arial', fontSize=9.1, leading=12.3, textColor=INK),
    'thead': ParagraphStyle('thead', fontName='Arial-Bold', fontSize=8.7, leading=11.8, textColor=white),
    'h2': ParagraphStyle('h2', fontName='Arial-Bold', fontSize=12.2, leading=16, textColor=TEAL, spaceBefore=8, spaceAfter=6),
    'h1': ParagraphStyle('h1', fontName='Arial-Bold', fontSize=23, leading=27, textColor=NAVY, spaceAfter=12),
    'eyebrow': ParagraphStyle('eyebrow', fontName='Arial-Bold', fontSize=8.5, leading=12, textColor=TEAL, spaceAfter=8),
    'callout': ParagraphStyle('callout', fontName='Arial', fontSize=10.3, leading=14.7, textColor=NAVY),
}

story = []
page_texts = []
current_text = []

def p(text, style='body'):
    current_text.append(text)
    return Paragraph(text, styles[style])

def add(text, style='body'):
    story.append(p(text, style))

def title(number, label, heading):
    if story:
        page_texts.append('\n\n'.join(current_text))
        current_text.clear()
        story.append(PageBreak())
    add(number + ' / ' + label.upper(), 'eyebrow')
    add(heading, 'h1')

def section(heading, text):
    add(heading, 'h2')
    add(text)

def box(text):
    t = Table([[p(text, 'callout')]], colWidths=[W])
    t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), PALE), ('BOX', (0,0),(-1,-1),0.6,LINE),
                          ('LEFTPADDING',(0,0),(-1,-1),12), ('RIGHTPADDING',(0,0),(-1,-1),12),
                          ('TOPPADDING',(0,0),(-1,-1),10), ('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story.extend([t, Spacer(1,9)])

def table(headers, rows, widths):
    data = [[p(v, 'thead') for v in headers]] + [[p(v, 'table') for v in row] for row in rows]
    t = Table(data, colWidths=[W*x for x in widths], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),NAVY), ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[white,PALE]),
        ('LINEBELOW',(0,0),(-1,0),0.6,NAVY), ('LINEBELOW',(0,1),(-1,-1),0.35,LINE),
        ('LEFTPADDING',(0,0),(-1,-1),9), ('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8),
    ]))
    story.extend([t, Spacer(1,9)])

# 1. Text that maps directly to the application form.
title('01', 'Proponente • desafio e descrição', 'Renda sustentável com\nOpen Finance'.replace('\n','<br/>'))
add('<b>Proposta de experimento | Sandbox CAIXA NEXT 2026</b>')
add('<b>Proponente:</b> Marthus Sera &nbsp;&nbsp; <b>Projeto:</b> Open Finance Income Estimator<br/>'
    '<b>Base prevista:</b> dados reais anonimizados &nbsp;&nbsp; <b>Versão:</b> 10/09/2026', 'small')
box('<b>Objetivo:</b> transformar o histórico financeiro autorizado em uma estimativa explicável de renda mensal recorrente, para apoiar a análise de renda e testar decisões mais bem fundamentadas sobre o relacionamento com o cliente.')
section('Desafio selecionado',
    '<b>InteligêncIA: Velocidade, Escala e Personalização.</b> A proposta se concentra na qualificação da tomada de decisão e no aumento da produtividade, com potencial de melhorar a experiência do cliente e a adequação futura de ofertas. [1, Anexo I]')
section('Problema e público',
    'Clientes com renda variável, múltiplas fontes de recebimento ou movimentação distribuída entre instituições podem apresentar uma visão fragmentada de sua renda. Somar todos os créditos também pode inflar a estimativa ao incluir transferências próprias, empréstimos, resgates e estornos. A frequência e o impacto dessa dor na CAIXA serão medidos no experimento.<br/>'
    'O público inicial será de pessoas físicas com histórico disponível; assalariados servirão de comparação e profissionais com renda variável serão avaliados como segmento específico. Os usuários diretos da solução serão profissionais responsáveis pela análise de renda.')
section('Solução proposta',
    'Combinar regras de classificação de transações e modelos de machine learning para identificar receitas recorrentes, excluir entradas que não representam renda e estimar um patamar mensal sustentável. O resultado apresentará valor estimado, evidências, qualidade do histórico e situações que exigem revisão humana.')
section('Definição e escopo',
    '<b>Renda sustentável</b> é o patamar mensal recorrente compatível com as fontes de renda vigentes na data da análise, descontando efeitos extraordinários. Não equivale à sobra após despesas, à capacidade de pagamento nem ao limite de crédito.<br/>'
    'O piloto será retrospectivo, em ambiente controlado, com dados anonimizados e sem alteração automática de crédito, cartões ou ofertas. O produto atual é um protótipo de pesquisa em Python; a execução proposta no Sandbox usará Databricks Lakehouse, AutoML pela interface low code e MLflow.')

# 2. A measurable research design, without presenting assumptions as achieved facts.
title('02', 'Hipótese e testes', 'O que será validado')
box('<b>Hipótese principal:</b> combinar evidências de transações com machine learning reduz o erro da estimativa de renda sustentável em relação a métodos determinísticos, sem ampliar de forma relevante a superestimação de renda e com menor esforço de análise.')
section('Hipóteses complementares',
    '<b>H2.</b> Acrescentar dados de outras instituições ao histórico CAIXA melhora a estimativa para parte dos clientes. O ganho será medido por comparação pareada, e não presumido a partir do número de contas.<br/>'
    '<b>H3.</b> Exibir evidências, limitações e alertas reduz o tempo de análise e facilita a compreensão pelos profissionais que avaliam renda.')
section('1. Preparar dados e referência de avaliação',
    'Obter amostra autorizada de dados reais anonimizados, preservando histórico longitudinal e vínculos necessários para reconhecer transferências, sem expor identidades. A referência de renda será produzida por analistas a partir de documentação e evidências independentes, com definição de renda recorrente vigente e resolução de divergências. Não será criada pela mesma fórmula usada pelo modelo.')
section('2. Reservar clientes e respeitar a data de corte',
    'Planejamento inicial: <b>300 clientes</b>, preferencialmente com até 12 meses de histórico. Separar 180 para desenvolvimento, 60 para validação/calibração e 60 para teste final, sem repetir clientes entre grupos. Usar uma data de referência por cliente no piloto; o teste incluirá período posterior ao desenvolvimento. Esses quantitativos são metas de planejamento, sujeitos à disponibilidade e à precisão estatística obtida.')
section('3. Comparar métodos nas mesmas condições',
    'Usar somente informações disponíveis até a data de referência. Comparar o procedimento de renda da CAIXA, quando disponibilizado, métodos simples e o modelo candidato sobre os mesmos clientes e janelas. No teste de fontes adicionais, manter o modelo fixo e comparar dados CAIXA com dados CAIXA acrescidos de outras instituições; apresentar também o resultado de cada baseline no respectivo cenário.')
section('4. Testar qualidade, incerteza e usabilidade',
    'Separar resultados por perfil de renda, extensão do histórico, lacunas e volatilidade. Testar entradas ambíguas, duplicidades, estornos e mudanças de renda. Avaliar intervalos e casos sem evidência suficiente. Em paralelo, realizar teste exploratório com 5 a 8 analistas, casos anonimizados pareados e ordem alternada, medindo tempo, conclusão da tarefa e clareza das explicações.')
add('A renda de períodos posteriores poderá funcionar como verificação complementar, registrando mudanças de regime. Ela não será tratada automaticamente como a verdade da renda sustentável na data de corte. O piloto não mede efeitos causais sobre inadimplência ou receita.', 'small')

# 3. Baseline is a comparator, not a dataset.
title('03', 'Baseline e resultados esperados', 'Como medir o sucesso')
add('A base de transações é a <b>fonte de dados</b>. O baseline é o <b>método de comparação</b>. Todos os métodos serão avaliados contra a mesma referência independente de renda.')
table(['Comparação', 'Definição'], [
    ['Processo atual CAIXA', 'Método vigente de renda presumida ou análise, se disponibilizado pela área responsável. Desempenho e tempo atuais ainda serão medidos.'],
    ['Baselines determinísticos', 'Renda classificada do último mês; média de fontes recorrentes dos últimos 3 meses; mediana da renda mensal observada em até 12 meses.'],
    ['Modelo candidato', 'Regressão supervisionada selecionada no Databricks AutoML. O estimador Python existente será uma referência adicional de pesquisa.'],
], [.29,.71])
add('Metas abaixo são <b>propostas para o experimento</b>, não exigências numéricas do edital nem resultados já alcançados. Serão registradas antes do teste final e ajustadas apenas na fase de planejamento.', 'small')
table(['Indicador', 'Meta inicial e regra de avaliação'], [
    ['Erro de renda', 'Reduzir o MAE em pelo menos 20% frente ao melhor baseline determinístico, escolhido na validação. Comparar também com o processo CAIXA, quando disponível.'],
    ['Superestimação', 'Medir a proporção de estimativas acima de 120% da referência positiva. Meta: aumento de no máximo 2 pontos percentuais frente ao baseline. Tratar referências zero separadamente.'],
    ['Intervalos e abstenção', 'Para intervalo nominal de 80%, buscar cobertura empírica entre 75% e 85% nos casos publicados, reportando largura, caudas e fração sem intervalo. Meta de publicação: pelo menos 80% dos casos elegíveis.'],
    ['Produtividade e clareza', 'Reduzir em 20% a mediana do tempo de análise, sem elevar erros de interpretação. Buscar avaliação de clareza de pelo menos 4 em 5 entre analistas participantes.'],
], [.29,.71])
section('Medição e decisão de continuidade',
    '<b>MAE:</b> média do erro absoluto em reais. <b>WAPE:</b> soma dos erros absolutos dividida pela soma das rendas de referência; não é uma taxa de acerto. Para referência total zero, o WAPE não será calculado.<br/>'
    'Apresentar intervalos de confiança de 95% por reamostragem de clientes e resultados segmentados. Para o limite de superestimação, exigir que o limite superior da diferença seja compatível com a margem de 2 pontos percentuais. Amostra insuficiente ou resultados inconclusivos exigem ampliação ou revisão do piloto, sem declarar sucesso.')
add('O ganho com dados adicionais será a diferença pareada de MAE entre os dois cenários. Não se pressupõe conhecer todas as contas ativas nem se corrige renda por uma fração estimada de recursos não compartilhados. [2]', 'small')

# 4. Read numeric evidence from the actual committed artifacts.
report = json.loads((ROOT/'estimator/training/artifacts/capacity-estimator-0.7.0-report.json').read_text())
test = report['evaluation']['test']
mae = test['candidate']['overall']['mean_absolute_error_minor']/100
base = test['historical_median_12m']['overall']['mean_absolute_error_minor']/100
assert report['customer_counts']['test'] == 109
assert test['candidate']['overall']['count'] == 1308
assert round(mae,2) == 253.74 and round(base,2) == 840.89
stress = json.loads((ROOT/'estimator/evaluation/baselines/stress-0.13.0-report.json').read_text())
suites = {s['suite']:s for s in stress['suites']}
assert round(suites['high_volatility']['sustainable_income']['wape']*100,2) == 40.82

title('04', 'Evidências existentes', 'O que o protótipo já demonstra')
box('<b>Maturidade atual: protótipo de pesquisa com avaliação sintética.</b> Há simulador, estimador executável, demonstração em Streamlit, explicações e artefatos versionados. Não há validação com clientes reais nem adaptador de provedor Open Finance concluído. [2]')
section('Resultado técnico registrado',
    'O modelo de renda sustentável <b>capacity-estimator 0.7.0</b> foi avaliado em 109 clientes sintéticos reservados para teste, totalizando 1.308 observações cliente-mês. A população completa possui 720 clientes: 510 de treinamento, 101 de validação e 109 de teste. As observações mensais não equivalem a clientes independentes. [3]')
table(['Método', 'MAE em reais', 'WAPE'], [
    ['Modelo de renda sustentável', 'R$ 253,74', '5,02%'],
    ['Mediana histórica de até 12 meses', 'R$ 840,89', '16,63%'],
    ['Média de fontes recorrentes de 3 meses', 'R$ 880,78', '17,42%'],
    ['Renda classificada do último mês', 'R$ 936,48', '18,52%'],
], [.57,.24,.19])
add('Nesse teste sintético, o MAE do modelo foi <b>69,82% menor</b> que o da mediana histórica, o melhor dos três baselines. Valores monetários do relatório foram convertidos de centavos para reais. Esse ganho não representa desempenho esperado na carteira CAIXA. [3]')
section('Limitações que orientam o Sandbox',
    'O relatório de estresse atual, com 20 clientes sintéticos e 240 observações por cenário, mostra perda importante de qualidade fora dos perfis familiares ao modelo. Para renda altamente volátil, o WAPE da renda sustentável chega a <b>40,82%</b>. [4]')
table(['Cenário de estresse', 'Cobertura do intervalo nominal de 80%'], [
    ['Transações com ruído e ambiguidades', '9,17% das 240 observações'],
    ['Renda altamente volátil', '19,17% das 240 observações'],
], [.57,.43])
add('Essas falhas impedem apresentar os intervalos atuais como confiáveis para dimensionar crédito. O piloto deverá recalibrar o modelo em dados reais, validar a classificação de entradas e definir revisão ou abstenção quando faltarem evidências. Métricas acima foram conferidas nos relatórios existentes; os treinamentos não foram reexecutados nesta preparação.', 'small')
box('<b>O que ainda precisa ser comprovado:</b> ganho em dados reais, robustez por segmento, redução do tempo de análise e utilidade para usuários. A expressão “confiabilidade superior a 90%” não é adotada, pois não há evidência que a sustente.')

# 5. The low-code element is central to the proposed experiment, not claimed as delivered.
title('05', 'Estratégia e normas', 'Arquitetura proposta e governança')
add('<b>Plataforma indicada pelo proponente:</b> Databricks Lakehouse + MLflow. Para materializar o requisito low code/no code do eixo de IA, propõe-se usar a <b>interface do Databricks AutoML como etapa central de treinamento, comparação e seleção de modelos</b>. A documentação identifica essa interface como low code. Disponibilidade e aceitação no Sandbox precisam ser confirmadas. [1, Anexo III; 5]')
table(['Etapa', 'Papel no experimento'], [
    ['1. Dados anonimizados', 'Extração controlada pela área custodiante, saneamento e verificação de qualidade. Sem conexão direta inicial aos sistemas transacionais.'],
    ['2. Databricks Lakehouse', 'Organização das tabelas de observações, atributos e referências, com separação de acesso e versões reproduzíveis.'],
    ['3. AutoML pela interface', 'Treinamento e seleção do regressor em base preparada, sem expor o teste final à escolha do modelo. Ajustes de preparação e avaliação poderão usar código.'],
    ['4. MLflow e apresentação', 'Registro de parâmetros, versões e métricas; comparação de execuções e relatório de renda com evidências. O componente existente será adaptado para esse fluxo.'],
], [.28,.72])
section('Proteção dos dados',
    'O experimento usará <b>dados reais anonimizados</b>, conforme proposta do proponente. A área custodiante deverá avaliar risco de reidentificação, retirar identificadores e tratar descrições livres, preservando apenas os vínculos e sinais necessários à avaliação. Substituir CPF por código, isoladamente, não demonstra anonimização. Se houver possibilidade de reidentificação, a base deverá ser tratada sob os controles aplicáveis a dados pessoais. [7]')
section('Regras de execução',
    'Validar autorização de uso e finalidade, origem dos dados, condições do compartilhamento Open Finance, acesso mínimo necessário, prazo de retenção e descarte. Os dados permanecerão no ambiente corporativo autorizado; relatórios e registros do MLflow deverão evitar informações identificáveis. O compartilhamento via Open Finance depende da autorização do cliente. [6]<br/>'
    'Submeter o experimento às diretrizes do MN OR219, de segurança, governança de dados e IA Responsável citadas no edital, além das políticas de crédito aplicáveis. Registrar versões, decisões de análise e limitações; manter revisão humana durante o piloto. [1]')
section('Alinhamento estratégico e complementaridade',
    'Contribuição proposta aos pilares Cliente no Centro e Tecnologia e Inovação: compreender melhor a renda, reduzir retrabalho e oferecer evidências consistentes. Enquadramento inicial <b>H1, inovação incremental</b>, sujeito à banca. Consultar a Fábrica de Modelos de Machine Learning e os portfólios internos para identificar complementaridade e evitar sobreposição com iniciativas estratégicas. [1]')

# 6. Feasibility and impact, framed as hypotheses rather than benefit claims.
title('06', 'Impactos, riscos e viabilidade', 'Valor esperado e execução enxuta')
add('<b>Para o cliente:</b> perspectiva de uma análise de renda mais contextualizada e menor necessidade de esclarecimentos repetidos. <b>Para a CAIXA:</b> potencial de reduzir esforço operacional e melhorar a fundamentação das análises. Aumento de receita, conversão ou redução de inadimplência permanecem hipóteses para estudos posteriores.')
table(['Risco', 'Tratamento proposto'], [
    ['Renda inflada ou incompleta', 'Validar transferências, estornos, empréstimos e resgates; distinguir ausência de renda de insuficiência de dados; revisar casos ambíguos.'],
    ['Viés e mudança de perfil', 'Medir erros e superestimação por segmento; testar renda irregular e mudanças de regime; recalibrar ou suspender uso quando o desempenho não se sustentar.'],
    ['Vazamento ou reidentificação', 'Validar anonimização com a área custodiante, restringir acessos e revisar dados, logs e resultados exportados.'],
    ['Dados, plataforma ou integração indisponíveis', 'Confirmar base, referência de renda e AutoML antes do piloto real. Usar extração em lote e limitar integrações. Sem esses recursos, reavaliar viabilidade e enquadramento.'],
], [.30,.70])
section('Plano alinhado à jornada',
    '<b>Bootcamp, 05/10 a 27/11:</b> validar a dor e a jornada, confirmar base e plataforma, definir referência de renda, baselines, metas, custos e visão da solução. Preparar evidências e pitch para o Demoday previsto em 29/11.<br/>'
    '<b>Summer Job, 18/01 a 26/02/2027, se selecionado:</b> preparar e validar dados; executar comparações no AutoML; testar explicações com analistas; avaliar resultados finais e entregar MVP experimental, documentação e recomendação de continuidade. Datas conforme Anexo II do documento recebido. [1]')
section('Equipe, recursos e estimativa financeira',
    'Marthus Sera será o responsável. A composição adicional, se houver, será informada no formulário, respeitado o limite de quatro integrantes. São necessários apoio do custodiante dos dados, interlocução com análise de renda/risco, workspace autorizado, AutoML habilitado, armazenamento e computação dimensionados para a amostra.<br/>'
    'Priorizar infraestrutura já disponível, processamento em lote, limites de execuções e desligamento do recurso computacional ocioso. Não há preço ou orçamento confirmado; o custo será estimado por <b>horas de equipe + computação + armazenamento + licenças incrementais</b>, antes da execução. A disponibilidade de recursos adicionais não será presumida.')
box('<b>Critério econômico:</b> estimar horas potencialmente poupadas como volume elegível × minutos poupados por análise ÷ 60. Multiplicar pelo custo-hora carregado e comparar com os custos incrementais. Reportar capacidade operacional liberada separadamente de economia financeira realizada.')

# 7. Submission guide deliberately distinct from proposed form copy.
title('07', 'Apoio ao proponente', 'Conferência antes da inscrição')
add('As páginas 1 a 6 fornecem conteúdo para os campos da proposta. Esta página reúne pendências administrativas e fontes da análise; não substitui o formulário nem os termos oficiais.', 'small')
box('<b>Prazo no edital recebido: 28/08 a 18/09/2026.</b><br/>'
    'A inscrição é feita pelo proponente em <link href="https://sandbox.caixa/sandbox/next2026.html" color="#007F86">sandbox.caixa/sandbox/next2026.html</link>, pelo botão “Inscreva-se”. O edital exige formulário e confirmação na plataforma; não estabelece o PDF como substituto. A primeira entrega tratada aqui é a inscrição, formalmente a 2ª etapa após a Formação. [1, itens 7 e 9; Anexo II]')
table(['Item a conferir', 'Situação nesta proposta'], [
    ['Identificação e participação', 'Marthus Sera informado. Completar matrícula, unidade, contatos e equipe conforme os campos do portal. Conferir condições do item 6.1 e impedimentos de participação dos itens 4.2, 5.5.1 e 12.5.'],
    ['Admissibilidade do eixo IA', 'IA, finalidade e métricas descritas. Confirmar acesso e adequação do AutoML, disponibilidade autorizada da base real e viabilidade de execução. Lakehouse + MLflow, isoladamente, não comprovam o requisito low code/no code.'],
    ['Disponibilidade e responsabilidade', 'Confirmar disponibilidade para a jornada e autorização da liderança para o Bootcamp. Ler e aceitar os termos oficiais, inclusive os de propriedade intelectual e uso de nome, voz e imagem. Nenhum aceite ou assinatura foi realizado por este documento.'],
    ['Envio e confirmação', 'Transpor conteúdo ao formulário, adequar aos limites de caracteres e verificar confirmação. Tais limites e eventuais atualizações não puderam ser conferidos no portal interno.'],
], [.29,.71])
add('O edital recebido contém divergências pontuais: o item 8.3 menciona 2025, enquanto o documento é de 2026; o item 14.11 diverge do fluxo de seleção do item 10.1. Para o planejamento foi usado o Anexo II; confirmar a versão vigente e o calendário no portal. A análise não atesta elegibilidade pessoal nem aprovação institucional.', 'small')
add('Fontes e rastreabilidade', 'h2')
add('<b>[1]</b> Edital 001/2026, <i>Caixa-Sandbox-NEXT-2026.pdf</i>, fornecido pelo proponente: campos (p. 8), critérios (pp. 10-12), desafio (pp. 20-21), cronograma (pp. 23-26) e eixo IA (pp. 29-30).<br/>'
    '<b>[2]</b> Repositório OPFIncomeEstimator: README; demo_app/README; docs/adr/0010-coverage-oracle-removed; contrato de alvos de renda. Resumo original: caixa_next_projeto_resumo.txt.<br/>'
    '<b>[3]</b> estimator/training/artifacts/capacity-estimator-0.7.0-report.json; avaliação test, 109 clientes e 1.308 observações.<br/>'
    '<b>[4]</b> estimator/evaluation/baselines/stress-0.13.0-report.json; cenários noisy e high_volatility. Foram priorizados os relatórios atuais sobre números históricos dos textos descritivos.<br/>'
    '<b>[5]</b> Databricks: <link href="https://docs.databricks.com/aws/en/machine-learning/automl/" color="#007F86">What is AutoML?</link> e <link href="https://docs.databricks.com/aws/en/machine-learning/automl/regression" color="#007F86">Regression with AutoML</link>. A plataforma/versão efetiva deverá ser verificada no ambiente CAIXA.<br/>'
    '<b>[6]</b> Banco Central: <link href="https://www.bcb.gov.br/meubc/faqs/s/open-finance" color="#007F86">Perguntas frequentes sobre Open Finance</link>.<br/>'
    '<b>[7]</b> ANPD: <link href="https://www.gov.br/participamaisbrasil/consulta-a-sociedade-estudo-preliminar-anonimizacao-e-pseudonimizacao-para-protecao-de-dados" color="#007F86">Estudo preliminar sobre anonimização e pseudonimização</link> (consulta pública). Fontes locais e públicas consultadas em 10/09/2026.', 'small')

page_texts.append('\n\n'.join(current_text))

def decorate(c, doc):
    c.saveState()
    width,height = A4
    c.setFillColor(NAVY)
    c.rect(0,height-9,width,9,fill=1,stroke=0)
    c.setFont('Arial-Bold',8)
    c.drawString(44,height-34,'CAIXA NEXT 2026  /  PROPOSTA DE EXPERIMENTO')
    c.setStrokeColor(LINE)
    c.line(44,43,width-44,43)
    c.setFillColor(MUTED)
    c.setFont('Arial',7.5)
    c.drawString(44,29,'Marthus Sera  |  Open Finance Income Estimator  |  10.09.2026')
    c.setFont('Arial-Bold',8)
    c.drawRightString(width-44,29,f'{doc.page:02d} / 07')
    c.restoreState()

doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=44, leftMargin=44,
    topMargin=57, bottomMargin=58, title='Renda sustentável com Open Finance - Proposta inicial CAIXA NEXT 2026',
    author='Marthus Sera', subject='Proposta de experimento para inscrição no Sandbox CAIXA NEXT 2026',
    pageCompression=1)
doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
(ROOT/'tmp/pdfs/proposal_content.txt').write_text('\n\n\f\n\n'.join(page_texts),encoding='utf-8')
print(OUT)
