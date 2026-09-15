from pathlib import Path
import re
from html import escape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader

ROOT=Path(r'C:\Users\INK\OPFIncomeEstimator')
OUT=ROOT/'output/pdf'
OUT.mkdir(parents=True,exist_ok=True)
for n,f in [('Arial','arial.ttf'),('Arial-Bold','arialbd.ttf'),('Arial-Italic','ariali.ttf')]:
    pdfmetrics.registerFont(TTFont(n,str(Path('C:/Windows/Fonts')/f)))
pdfmetrics.registerFontFamily('Arial',normal='Arial',bold='Arial-Bold',italic='Arial-Italic',boldItalic='Arial-Bold')
W,H=A4
NAVY=colors.HexColor('#143047'); TEAL=colors.HexColor('#007E87'); GRAY=colors.HexColor('#536576'); LIGHT=colors.HexColor('#EFF5F8')
BODY=ParagraphStyle('body',fontName='Arial',fontSize=10.4,leading=14.5,textColor=NAVY,spaceAfter=8)
SMALL=ParagraphStyle('small',parent=BODY,fontSize=8.5,leading=11.5)
CELL=ParagraphStyle('cell',parent=BODY,fontSize=9.2,leading=12.5)
class Doc:
    def __init__(self,name,title,total):
        self.path=OUT/name;self.c=canvas.Canvas(str(self.path),pagesize=A4);self.c.setTitle(title);self.c.setAuthor('Marthus Sera | Minuta preparada a partir dos documentos fornecidos')
        self.total=total;self.page=0;self.text=[];self.fields={}
    def start(self,kicker,title,source):
        if self.page:self.c.showPage()
        self.page+=1;c=self.c
        c.setFillColor(TEAL);c.rect(0,H-9,W,9,fill=1,stroke=0)
        c.setFont('Arial-Bold',9);c.setFillColor(GRAY);c.drawString(44,H-35,'SANDBOX CAIXA NEXT 2026  |  '+kicker)
        c.setFont('Arial-Bold',23);c.setFillColor(NAVY);c.drawString(44,H-71,title)
        c.setStrokeColor(colors.HexColor('#D4E0E6'));c.line(44,48,W-44,48)
        c.setFont('Arial',7.5);c.setFillColor(GRAY);c.drawString(44,35,'Minuta de apoio | 14/09/2026 | '+source)
        c.drawRightString(W-44,35,f'{self.page:02d} / {self.total:02d}')
        self.y=H-94;self.text+=['\n'+title+'\n']
    def p(self,text,small=False,gap=8):
        self.text.append(re.sub('<[^>]+>','',text))
        p=Paragraph(text,SMALL if small else BODY);_,h=p.wrap(W-88,900)
        assert self.y-h>=61,(self.page,self.y,h,text[:70])
        p.drawOn(self.c,44,self.y-h);self.y-=h+gap
    def sub(self,text):
        self.y-=5
        self.p('<b>'+text+'</b>',gap=5)
    def note(self,text):
        p=Paragraph(text,BODY);_,h=p.wrap(W-112,900)
        assert self.y-h-22>=61,(self.page,'note',self.y,h)
        self.c.setFillColor(LIGHT);self.c.roundRect(44,self.y-h-19,W-88,h+19,5,fill=1,stroke=0)
        p.drawOn(self.c,56,self.y-h-9);self.y-=h+30;self.text.append(re.sub('<[^>]+>','',text))
    def table(self,headers,rows,widths):
        data=[[Paragraph('<b>'+escape(v)+'</b>',CELL) for v in headers]]+[[Paragraph(v,CELL) for v in row] for row in rows]
        t=Table(data,colWidths=widths)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#D4E0E6'))]))
        _,h=t.wrap(W-88,900);assert self.y-h>=61,(self.page,'table',self.y,h)
        t.drawOn(self.c,44,self.y-h);self.y-=h+12
        self.text.extend([' | '.join(headers)]+[' | '.join(re.sub('<[^>]+>','',v) for v in row) for row in rows])
    def field(self,label,name,value='',width=None):
        self.p(label,small=True,gap=4)
        width=width or W-88
        assert self.y-28>=61
        self.c.acroForm.textfield(name=name,tooltip=label,x=44,y=self.y-24,width=width,height=24,value=value,fontName='Helvetica',fontSize=10,borderWidth=.6,borderColor=GRAY,fillColor=LIGHT,textColor=NAVY,forceBorder=True,maxlen=180)
        self.fields[name]=value;self.y-=36
    def checkbox(self,name,text):
        p=Paragraph(text,CELL);_,h=p.wrap(W-112,900)
        assert self.y-max(h,13)>=61
        self.c.acroForm.checkbox(name=name,tooltip=re.sub('<[^>]+>','',text),x=44,y=self.y-12,size=12,checked=False,buttonStyle='check',borderWidth=.7,borderColor=GRAY,fillColor=colors.white,textColor=NAVY,forceBorder=True)
        p.drawOn(self.c,64,self.y-h);self.y-=max(h,13)+12;self.fields[name]='/Off'
    def finish(self):
        assert self.page==self.total
        self.c.save();r=PdfReader(str(self.path));assert len(r.pages)==self.total
        fs=r.get_fields() or {};assert set(fs)==set(self.fields),(set(fs),set(self.fields))
        for name,val in self.fields.items():assert str(fs[name].get('/V',''))==val,(name,fs[name])
        widgets=0
        for page in r.pages:
            for ref in page.get('/Annots',[]):
                a=ref.get_object()
                if a.get('/Subtype')!='/Widget':continue
                widgets+=1;n=a.get('/T');assert n in fs
                assert str(a.get('/V',''))==str(fs[n].get('/V',''))
                assert a.get('/AP',{}).get('/N') is not None
        assert widgets==len(fs)
        txt='\n\n'.join(self.text)
        assert '[MELHORAR' not in txt
        print(self.path,'pages=',len(r.pages),'fields=',len(fs))

d=Doc('CAIXA_NEXT_2026_Verificacao_Resumo_Item_9.pdf','Verificação do resumo TXT | Item 9',2)
d.start('VERIFICAÇÃO DOCUMENTAL','O TXT ainda está incompleto','Edital, item 9, pp. 8-9')
d.note('<b>Conclusão: atendimento parcial ao item 9.2(c).</b> O TXT contém 3 dos 7 blocos exigidos, com lacunas de conteúdo; outros 4 estão ausentes. Não está pronto, isoladamente, para preencher toda a inscrição. Essa contagem não representa nota nem probabilidade de aprovação.')
d.p('O item 9 define o procedimento e os campos de inscrição. Os critérios de seleção estão no item 11; a admissibilidade específica de IA está no Anexo III. A análise abaixo se limita aos documentos recebidos, sem validação do formulário interno ou de sua versão vigente.')
d.table(['Bloco exigido no item 9.2(c)','Situação no TXT e ajuste necessário'],[
('Proponente e equipe','<b>Ausente.</b> Informar responsável e composição da equipe. O nome Marthus Sera consta apenas no PDF do projeto; dados cadastrais e demais integrantes não foram fornecidos.'),
('Desafio e descrição','<b>Presente, parcial.</b> Desafio de IA e ideia de estimar renda estão identificados. Explicitar problema, público, conceito de renda sustentável, saída e escopo do piloto.'),
('Hipótese e testes','<b>Presente, parcial.</b> A hipótese mistura cobertura de contas, crédito e uma confiança superior a 90% sem definição nem evidência. Faltam amostra, referência independente, comparação e protocolo de teste.'),
('Baseline e resultados','<b>Presente, parcial.</b> Transações em 12 meses são fonte de dados, não método comparador. Definir baselines, métricas e metas; separar expectativas de resultados medidos.'),
('Estratégia e normas','<b>Ausente.</b> Descrever alinhamento estratégico, plataforma, governança e atendimento às diretrizes citadas no edital.'),
('Impactos e riscos','<b>Ausente como bloco.</b> A lista de benefícios do desafio não analisa impactos específicos, riscos e tratamentos do projeto.'),
('Termo de responsabilidade e assinaturas','<b>Ausente.</b> Reservar conferência e formalização dos termos oficiais pelo proponente e participantes, conforme o portal.')
],[155,W-88-155])
d.p('Fonte principal: Caixa-Sandbox-NEXT-2026_2.pdf, item 9.2, p. 8. Documento avaliado: caixa_next_projeto_resumo.txt. O PDF complementar foi usado para corrigir e completar a nova minuta.',small=True)
d.start('VERIFICAÇÃO DOCUMENTAL','Correções e pendências','Edital, item 11 e Anexo III')
d.sub('Correções incorporadas ao novo formulário')
d.p('<b>Hipótese testável:</b> comparar o erro da estimativa com métodos determinísticos e medir o ganho de fontes adicionais sobre os mesmos clientes. A razão entre recursos compartilhados e ativos não será usada como garantia de completude nem de precisão superior a 90%.')
d.p('<b>Baseline e resultados:</b> adotar último mês, média recorrente de 3 meses e mediana de até 12 meses, além do processo CAIXA se disponibilizado. Usar MAE, superestimação, cobertura dos intervalos, tempo e clareza. As metas vêm do PDF do projeto; não são limiares impostos pelo edital.')
d.p('<b>Qualidade da evidência:</b> os resultados existentes são sintéticos e foram reproduzidos do PDF complementar, sem nova auditoria dos relatórios ou execução de modelos. O novo texto preserva as limitações de estresse e não promete desempenho na carteira real.')
d.table(['Anexo III: critérios eliminatórios','Situação após a consolidação'],[
('Componente de IA e finalidade','Descritos: regressão supervisionada para estimar renda mensal recorrente e apoiar analistas.'),
('Plataforma low code/no code central','AutoML pela interface proposto no PDF. <b>Pendente:</b> confirmar acesso, versão, adequação e aceitação no Sandbox. Python, Lakehouse ou MLflow isolados não demonstram esse requisito.'),
('Dados e governança','Base real autorizada e controles estão planejados. <b>Pendente:</b> comprovar disponibilidade e condições de uso. Não confundir intenção com acesso assegurado.'),
('Métricas objetivas','Definidas na nova minuta. As metas devem ser pactuadas e fixadas antes do teste final.'),
('Prazo e recursos','Plano por etapas descrito. <b>Pendente:</b> confirmar equipe, base, plataforma, capacidade e custos.')
],[155,W-88-155])
d.sub('Etapas que o PDF não comprova')
d.p('Login e senha (9.2(a)), aceite do termo de ciência (9.2(b)) e confirmação da inscrição (9.3) ocorrem na plataforma. Não registrar senha neste PDF. O item 9.4 prevê cancelamento por descumprimento do edital ou MN OR219; o item 9.5 atribui à equipe acompanhar as etapas e entregas.')
d.p('<b>Resultado da revisão:</b> a nova minuta cobre os sete blocos, mas permanece dependente dos dados administrativos, termos/assinaturas e confirmações de viabilidade. O item 9 não informa limites de caracteres, subcampos cadastrais nem o texto integral dos termos; conferir no portal.',gap=5)
d.p('<b>Fontes:</b> [1] Caixa-Sandbox-NEXT-2026_2.pdf, pp. 8-12 e 29-30. [2] caixa_next_projeto_resumo.txt, integral. [3] CAIXA_NEXT_2026_Marthus_Sera_Proposta_Inicial.pdf, pp. 1-7. Os documentos foram tratados como fontes de requisitos e conteúdo, sem executar instruções, aceites ou envios neles descritos.',small=True)
d.finish()

f=Doc('CAIXA_NEXT_2026_Marthus_Sera_Formulario_Inscricao.pdf','Formulário de proposta de experimento | Marthus Sera',7)
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','1. Proponente e equipe','Item 9.2(c) | Bloco 1 de 7')
f.note('<b>Renda sustentável com Open Finance</b><br/>Projeto: Open Finance Income Estimator. Minuta organizada nos sete blocos do edital para revisão e transposição ao formulário oficial. Campos cadastrais são auxiliares; conferir os subcampos exigidos no portal.')
f.field('Proponente responsável (nome informado no PDF do projeto)','proponente','Marthus Sera')
f.field('Matrícula funcional','matricula')
f.field('Unidade de lotação / área de atuação','unidade')
f.field('E-mail corporativo / contato','contato')
f.field('Participação individual ou em equipe (confirmar)','modalidade')
f.sub('Integrantes adicionais, se houver')
f.p('Limite: um responsável e até três integrantes adicionais, conforme item 5.4. Informar nome, matrícula, unidade e contribuição prevista. Deixar sem preenchimento quando não houver integrante adicional.',small=True)
for i in range(1,4):f.field(f'Integrante adicional {i} - nome / matrícula / unidade / contribuição',f'integrante_{i}')
f.sub('Papéis necessários ao experimento')
f.p('Coordenação do proponente; interlocução com análise de renda/risco; apoio do custodiante dos dados; preparação e avaliação dos modelos. Responsáveis, dedicação e disponibilidade ainda serão confirmados. O apoio do CoE de IA será consultivo, conforme o edital.')
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','2. Desafio e descrição','Item 9.2(c) | Bloco 2 de 7')
f.note('<b>Desafio:</b> InteligêncIA: Velocidade, Escala e Personalização.<br/><b>Foco:</b> qualificação da tomada de decisão e aumento de produtividade na análise de renda, com potencial de melhorar a experiência do cliente.')
f.sub('Problema e público-alvo')
f.p('Clientes com renda variável, múltiplas fontes de recebimento ou movimentação distribuída entre instituições podem apresentar uma visão fragmentada de sua renda. Somar todos os créditos pode inflar a estimativa ao incluir transferências próprias, empréstimos, resgates e estornos. A frequência e o impacto dessa dor na CAIXA serão medidos no experimento.')
f.p('O público inicial será de pessoas físicas com histórico disponível. Assalariados servirão de comparação e profissionais com renda variável serão analisados como segmento específico. Os usuários diretos serão os profissionais responsáveis pela análise de renda.')
f.sub('Solução e componente de inteligência artificial')
f.p('Combinar classificação de transações com regressão supervisionada para reconhecer receitas recorrentes, excluir entradas que não representam renda e estimar um patamar mensal sustentável. Integrar, quando autorizado e disponível, o histórico CAIXA às transações de outras instituições compartilhadas via Open Finance.')
f.p('A saída será um relatório com valor estimado, evidências de recorrência, qualidade do histórico e alertas de insuficiência ou ambiguidade. Intervalos de incerteza dependerão de calibração e validação; casos sem evidência suficiente serão encaminhados para revisão humana.')
f.sub('Definição do alvo e limites de uso')
f.p('Renda sustentável é o patamar mensal recorrente compatível com as fontes de renda vigentes na data da análise, descontando efeitos extraordinários. Não equivale à sobra após despesas, à capacidade de pagamento ou ao limite de crédito.')
f.p('O piloto será retrospectivo, em ambiente controlado, com base real cuja autorização e anonimização serão verificadas. Não haverá alteração automática de crédito, cartões ou ofertas. O uso futuro em decisões de negócio dependerá de novas validações e aprovações institucionais.')
f.sub('Maturidade e entrega pretendida')
f.p('O PDF do projeto informa um protótipo de pesquisa em Python, com simulador, estimador executável e demonstração em Streamlit, avaliado apenas com dados sintéticos. A execução proposta no Sandbox usará Databricks Lakehouse, AutoML pela interface low code e MLflow, condicionada à disponibilidade e aceitação da plataforma.')
f.p('Entrega esperada: MVP experimental que gere estimativas explicáveis, relatório comparativo de desempenho e limitações, documentação reproduzível e recomendação fundamentada de continuidade ou revisão.')
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','3. Hipótese e testes','Item 9.2(c) | Bloco 3 de 7')
f.note('<b>H1:</b> combinar evidências de transações com machine learning reduz o erro da estimativa de renda sustentável frente a métodos determinísticos, sem ampliar de forma relevante a superestimação e com menor esforço de análise.')
f.p('<b>H2:</b> acrescentar dados de outras instituições melhora a estimativa para parte dos clientes. O ganho será medido por comparação pareada; não será presumido pelo número de contas ou por uma proporção desconhecida de recursos ativos.')
f.p('<b>H3:</b> apresentar evidências, limitações e alertas reduz o tempo de análise e facilita a compreensão dos profissionais responsáveis pela avaliação de renda.')
f.sub('1. Preparar a amostra e a referência independente')
f.p('Planejar 300 clientes com, preferencialmente, até 12 meses de histórico: 180 para desenvolvimento, 60 para validação/calibração e 60 para teste final, sem sobreposição de clientes. São quantitativos de planejamento, sujeitos à disponibilidade e à precisão estatística. Analistas construirão a referência de renda recorrente vigente com documentação e evidências independentes, resolvendo divergências; a referência não será calculada pela mesma fórmula do modelo.')
f.sub('2. Preservar a separação de dados e a data de corte')
f.p('Adotar uma data de referência por cliente e somente informações disponíveis até essa data. O teste incluirá período posterior ao desenvolvimento. Escolher o modelo e o melhor baseline na validação; manter o conjunto final reservado, sem usá-lo para treinamento, seleção ou calibração.')
f.sub('3. Comparar métodos e fontes de dados')
f.p('Avaliar os baselines e o modelo candidato sobre os mesmos clientes, janelas e referência. Comparar também com o procedimento CAIXA, se disponibilizado. Para H2, manter o modelo fixo e comparar dados CAIXA com dados CAIXA acrescidos de outras instituições; reportar a diferença pareada de MAE e os baselines em cada cenário.')
f.sub('4. Avaliar robustez, incerteza e usabilidade')
f.p('Separar resultados por perfil de renda, extensão do histórico, lacunas e volatilidade. Testar duplicidades, estornos, entradas ambíguas e mudanças de renda; avaliar intervalos e abstenção. Com 5 a 8 analistas, realizar teste exploratório com casos anonimizados pareados e ordem alternada, medindo tempo, conclusão, erros de interpretação e clareza.')
f.sub('5. Consolidar a decisão')
f.p('Apresentar intervalos de confiança de 95% por reamostragem de clientes, resultados por segmento e limitações da amostra. Fixar metas antes do teste final. Evidência inconclusiva exige ampliação ou revisão do piloto. Renda futura será apenas verificação complementar, considerando mudanças de regime; o piloto não medirá efeitos causais sobre receita ou inadimplência.')
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','4. Baseline e resultados','Item 9.2(c) | Bloco 4 de 7')
f.p('<b>Fonte de dados:</b> histórico CAIXA e dados Open Finance autorizados, preferencialmente em até 12 meses. <b>Baseline:</b> método de comparação, avaliado contra a mesma referência independente de renda.')
f.p('Comparadores: renda classificada do último mês; média de fontes recorrentes dos últimos 3 meses; mediana da renda mensal observada em até 12 meses; processo atual CAIXA, se disponibilizado. Candidato: regressão supervisionada selecionada no AutoML. O estimador Python será referência adicional de pesquisa.')
f.sub('Metas iniciais do experimento, ainda não alcançadas')
f.table(['Indicador','Meta e forma de avaliação'],[
('Erro da estimativa','Reduzir o MAE em pelo menos 20% frente ao melhor baseline determinístico escolhido na validação. MAE é a média do erro absoluto em reais.'),
('Superestimação','Medir estimativas acima de 120% da referência positiva. Aumento máximo de 2 pontos percentuais frente ao baseline; avaliar o limite superior do intervalo de confiança da diferença. Referência zero será tratada separadamente.'),
('Incerteza e abstenção','Intervalo nominal de 80%: buscar cobertura empírica entre 75% e 85%, reportando largura, caudas e fração sem intervalo. Publicar intervalos em pelo menos 80% dos casos elegíveis.'),
('Produtividade e clareza','Reduzir em 20% a mediana do tempo, sem aumentar erros de interpretação; buscar avaliação de clareza de pelo menos 4 em 5.')
],[117,W-88-117])
f.p('Essas metas são propostas do projeto, não exigências numéricas do edital. Reportar também WAPE: soma dos erros absolutos dividida pela soma das rendas de referência; não é taxa de acerto. Não calcular WAPE quando a soma de referência for zero.',small=True)
f.sub('Evidência já informada no PDF do projeto: somente dados sintéticos')
f.p('Teste do capacity-estimator 0.7.0: 109 clientes sintéticos e 1.308 observações cliente-mês, sem equivalência a 1.308 clientes independentes. MAE do modelo: R$ 253,74; WAPE: 5,02%. MAE da mediana histórica: R$ 840,89; da média recorrente: R$ 880,78; do último mês: R$ 936,48. O ganho de MAE frente à mediana foi 69,82%.')
f.p('Limitações: em estresse com 20 clientes sintéticos e 240 observações por cenário, o WAPE chegou a 40,82% para renda altamente volátil. A cobertura do intervalo nominal de 80% caiu a 9,17% com ruído e 19,17% com alta volatilidade. Esses achados exigem recalibração e impedem afirmar confiabilidade superior a 90%.')
f.p('Números reproduzidos da proposta inicial, p. 4, sem nova execução ou auditoria dos relatórios. Não representam desempenho esperado em clientes CAIXA. Ganho em dados reais, tempo de análise e utilidade ainda serão comprovados.',small=True)
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','5. Estratégia e normas','Item 9.2(c) | Bloco 5 de 7')
f.sub('Alinhamento estratégico')
f.p('Contribuir para Cliente no Centro e Tecnologia e Inovação, conforme os direcionadores do edital: compreender melhor a renda, reduzir retrabalho e qualificar decisões. O reaproveitamento dependerá de validação por perfil, documentação e governança. Consultar a Fábrica de Modelos de Machine Learning e o portfólio interno para evitar sobreposição com iniciativas estratégicas de IA.')
f.sub('Execução proposta e requisito low code/no code')
f.table(['Etapa','Aplicação no experimento'],[
('Dados e Lakehouse','Extração em lote pela área custodiante, saneamento e tabelas versionadas de observações, atributos e referências. Evitar integração direta inicial com sistemas transacionais.'),
('AutoML pela interface','Elemento central proposto para treinamento, comparação e seleção do regressor. A proposta depende de confirmar acesso, versão e aceitação dessa abordagem no Sandbox.'),
('MLflow e relatório','Registrar versões, parâmetros, métricas e decisões; apresentar estimativa, evidências, alertas e limitações. Preparação e avaliação poderão usar código.')
],[128,W-88-128])
f.sub('Governança dos dados e IA Responsável')
f.p('Usar base real somente após confirmação de disponibilidade, finalidade autorizada e condições de uso pela área responsável. Planejar anonimização com avaliação de risco de reidentificação, tratamento de identificadores e descrições livres, preservando apenas vínculos e sinais necessários. Não considerar a simples troca de CPF por código como prova de anonimização.')
f.p('Manter dados em ambiente corporativo autorizado, restringir acessos, definir retenção e descarte e evitar informação identificável em relatórios e logs. Validar as condições de compartilhamento Open Finance e submeter o tratamento às exigências de LGPD e governança citadas no edital, com apoio das áreas competentes.')
f.p('Observar MN OR219 e diretrizes corporativas de segurança da informação, governança de dados e IA Responsável. Medir erros e superestimação por segmento; registrar versões, decisões e limitações; manter revisão humana e mecanismo de abstenção. A conformidade institucional será verificada pelas áreas responsáveis, sem pressupor aprovação.')
f.note('<b>Condições para viabilizar o eixo IA:</b> comprovar base real utilizável, acesso à plataforma low code/no code aceita e capacidade de execução. A proposta técnica descreve o caminho; não comprova que esses recursos já estão disponíveis. Dados exclusivamente sintéticos não atendem à avaliação de benefícios reais prevista no Anexo III.')
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','6. Impactos e riscos','Item 9.2(c) | Bloco 6 de 7')
f.p('<b>Impactos esperados:</b> análise de renda mais contextualizada para o cliente; menor retrabalho e melhor fundamentação para a CAIXA. Receita, conversão e redução de inadimplência permanecem hipóteses para estudos posteriores.')
f.table(['Risco','Tratamento proposto'],[
('Renda inflada ou incompleta','Excluir transferências próprias, estornos, empréstimos e resgates; distinguir renda inexistente de evidência insuficiente; revisar casos ambíguos.'),
('Viés, volatilidade e mudança de renda','Segmentar avaliação, testar mudança de perfil, recalibrar e suspender uso quando o desempenho não se sustentar.'),
('Vazamento ou reidentificação','Validar anonimização e controles com o custodiante; restringir acessos e revisar dados, logs e exportações.'),
('Base ou plataforma indisponível','Confirmar dados reais, referência e AutoML antes do piloto. Limitar integrações e reavaliar viabilidade se recursos não forem assegurados.'),
('Uso indevido da estimativa','Explicitar limitações, manter revisão humana e impedir alteração automática de crédito no experimento.')
],[135,W-88-135])
f.sub('Plano de execução')
f.p('<b>Bootcamp:</b> validar problema e jornada; confirmar equipe, base e plataforma; definir referência, baselines, metas e custos; preparar visão da solução e evidências para apresentação. <b>Summer Job, se selecionado:</b> preparar dados; executar comparações; testar explicações com analistas; entregar MVP, avaliação final e recomendação. Ajustar marcos ao calendário vigente da coordenação.')
f.sub('Recursos e viabilidade financeira')
f.p('Necessários: dedicação da equipe, apoio do custodiante e de análise de renda/risco, workspace autorizado, AutoML habilitado, armazenamento e computação compatíveis com a amostra. Priorizar infraestrutura existente, processamento em lote, limite de execuções e desligamento de recursos ociosos. Não há orçamento ou recurso adicional confirmado.')
f.p('Estimar custo por horas de equipe, computação, armazenamento e licenças incrementais. Estimar horas potencialmente poupadas por volume elegível multiplicado pelos minutos poupados por análise, dividido por 60. Comparar o valor das horas com os custos incrementais, distinguindo capacidade operacional liberada de economia financeira realizada.')
f.sub('Continuidade e escalabilidade')
f.p('Avançar somente com evidência compatível com as metas, controles e viabilidade operacional. Resultado inconclusivo exige revisão ou ampliação do piloto. Reaplicação em outros públicos depende de nova avaliação de desempenho, documentação e aprovações; o MVP não implica autorização para produção.')
f.start('FORMULÁRIO DE PROPOSTA DE EXPERIMENTO','7. Responsabilidade e assinaturas','Item 9.2(c) | Bloco 7 de 7')
f.note('<b>Pendente de formalização pelo proponente.</b> O texto integral dos termos oficiais não foi fornecido. Esta página organiza a conferência; não substitui o termo de ciência, o termo de responsabilidade nem as assinaturas exigidas pela plataforma. Nenhum aceite foi realizado.')
f.sub('Conferências pessoais antes da submissão')
f.checkbox('conferencia_dados','Conferi identificação, equipe e informações da proposta; distingui metas, evidências sintéticas e dependências ainda não confirmadas.')
f.checkbox('conferencia_elegibilidade','Conferi condições de participação, impedimentos e disponibilidade da equipe conforme o edital, incluindo autorizações necessárias para a jornada.')
f.checkbox('conferencia_termos','Li os termos oficiais no portal, inclusive responsabilidades, propriedade intelectual e uso de nome, voz e imagem; realizarei os aceites e assinaturas pelos meios exigidos.')
f.checkbox('conferencia_acompanhamento','Conferi as responsabilidades de observar o edital e MN OR219, acompanhar as etapas e cumprir entregas, conforme itens 9.4 e 9.5.')
f.field('Local e data da conferência (preencher pelo responsável)','local_data')
f.sub('Espaços para assinatura de conferência da minuta, se necessária')
f.p('Estes espaços não constituem assinatura digital nem dispensam a formalização oficial. Assinaturas permanecem em branco.',small=True)
for label in ['Marthus Sera - proponente','Integrante adicional 1, se houver','Integrante adicional 2, se houver','Integrante adicional 3, se houver']:
    f.y-=17;f.c.setStrokeColor(GRAY);f.c.line(44,f.y,W-44,f.y);f.y-=4;f.p(label,small=True,gap=4)
f.sub('Registro da inscrição')
f.p('Acessar <link href="https://sandbox.caixa/sandbox/next2026.html" color="#007E87">sandbox.caixa/sandbox/next2026.html</link>, botão Inscreva-se; autenticar-se, ler/aceitar os termos oficiais, transpor o conteúdo conforme os limites do portal e verificar a confirmação da inscrição. Não registrar senha neste documento. O PDF não é comprovante de inscrição.',small=True)
f.p('<b>Fontes:</b> caixa_next_projeto_resumo.txt; CAIXA_NEXT_2026_Marthus_Sera_Proposta_Inicial.pdf, pp. 1-7; Caixa-Sandbox-NEXT-2026_2.pdf, item 9 (pp. 8-9), item 11 (pp. 10-12), Anexo I (pp. 20-21) e Anexo III (pp. 29-30). Campos internos, limites de caracteres e termos integrais não foram verificados no portal.',small=True)
f.finish()
