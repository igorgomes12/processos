import asyncio
import re
import base64
import urllib.request
import os
from io import BytesIO
from typing import Dict, Any, Optional

from google.genai.types import Part, Blob
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, ListFlowable, ListItem, Image
from reportlab.lib import colors


# Debug mode (ativa logs detalhados)
DEBUG_MERMAID = os.getenv('DEBUG_MERMAID', 'false').lower() == 'true'


def _debug_log(message: str):
    """Imprime log apenas se DEBUG_MERMAID estiver ativo."""
    if DEBUG_MERMAID:
        print(f"[DEBUG] {message}")


def _clean_mermaid_code(mermaid_code: str) -> str:
    """
    Limpa e normaliza código Mermaid mal formatado.
    
    IMPORTANTE: Mermaid requer que cada declaração/conexão esteja em linha separada.
    
    Estratégia:
    1. Remove TODAS as quebras de linha (elimina quebras dentro de labels)
    2. Adiciona quebra APÓS cada comando completo (após ] ou })
    """
    original = mermaid_code.strip()
    
    _debug_log(f"Código Mermaid original ({len(original)} chars):")
    _debug_log(f"Primeiras 200 chars: {original[:200]}")
    
    # Passo 1: Remove TODAS as quebras de linha e múltiplos espaços
    single_line = ' '.join(original.split())
    
    # Passo 2: Identifica a declaração inicial
    match = re.match(r'(flowchart\s+\w+|graph\s+\w+|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt)\s+(.+)', single_line, re.IGNORECASE)
    
    if not match:
        return single_line
    
    diagram_type = match.group(1)
    rest = match.group(2)
    
    #Passo 3: Adiciona quebra de linha APÓS cada fechamento de label/decisão
    # Padrão: ], ), ou } seguido de espaço e letra maiúscula → adiciona \n
    # Isso separa comandos como "A[...] B[...]" ou "A(...) B(...)" em duas linhas
    formatted = rest
    
    # Substitui "] X[", "] X(", "] X{" por "]\nX[", "]\nX(", "]\nX{"
    formatted = re.sub(r'\]\s+([A-Z][\[{(])', r']\n\1', formatted)
    
    # Substitui ") X[", ") X(", ") X{" por ")\nX[", ")\nX(", ")\nX{"
    formatted = re.sub(r'\)\s+([A-Z][\[{(])', r')\n\1', formatted)
    
    # Substitui "} X[", "} X(", "} X{" por "}\nX[", "}\nX(", "}\nX{"
    formatted = re.sub(r'\}\s+([A-Z][\[{(])', r'}\n\1', formatted)
    
    # Substitui "], X ", ") X ", "} X " (referência a nó já definido) por quebra antes do nó
    # Exemplo: "C -- Não --> E E --> F" deve virar "C -- Não --> E\nE --> F"
    formatted = re.sub(r'\]\s+([A-Z])\s+', r']\n\1 ', formatted)
    formatted = re.sub(r'\)\s+([A-Z])\s+', r')\n\1 ', formatted)
    formatted = re.sub(r'\}\s+([A-Z])\s+', r'}\n\1 ', formatted)
    
    # Separa nós referenciados que ficam juntos: "E E -->" deve virar "E\nE -->"
    # Pattern: nó (letra maiúscula) + espaço + outro nó + seta
    formatted = re.sub(r'([A-Z])\s+([A-Z]\s*(?:-->|--|==|\.\.>))', r'\1\n\2', formatted)
    
    # Monta o código completo
    cleaned = f"{diagram_type}\n{formatted}"
    
    # Passo 4: Validação - verifica balanceamento
    open_brackets = cleaned.count('[')
    close_brackets = cleaned.count(']')
    open_braces = cleaned.count('{')
    close_braces = cleaned.count('}')
    open_parens = cleaned.count('(')
    close_parens = cleaned.count(')')
    
    if open_brackets != close_brackets:
        _debug_log(f"⚠️  Colchetes desbalanceados: {open_brackets} abre, {close_brackets} fecha")
        if open_brackets > close_brackets:
            missing = open_brackets - close_brackets
            cleaned += ']' * missing
            _debug_log(f"✅ Adicionados {missing} colchete(s) de fechamento")
    
    if open_braces != close_braces:
        _debug_log(f"⚠️  Chaves desbalanceadas: {open_braces} abre, {close_braces} fecha")
        if open_braces > close_braces:
            missing = open_braces - close_braces
            cleaned += '}' * missing
            _debug_log(f"✅ Adicionadas {missing} chave(s) de fechamento")
    
    if open_parens != close_parens:
        _debug_log(f"⚠️  Parênteses desbalanceados: {open_parens} abre, {close_parens} fecha")
        if open_parens > close_parens:
            missing = open_parens - close_parens
            cleaned += ')' * missing
            _debug_log(f"✅ Adicionados {missing} parêntese(s) de fechamento")
    
    _debug_log(f"Código NORMALIZADO ({len(cleaned)} chars):")
    if DEBUG_MERMAID:
        print(f"[DEBUG] --- CÓDIGO COMPLETO ---")
        print(cleaned)
        print(f"[DEBUG] --- FIM ---")
    
    return cleaned


def _render_mermaid_image(mermaid_code: str, max_width: float = 5.5 * inch, max_height: float = 9.5 * inch) -> Optional[Image]:
    """
    Renderiza um diagrama Mermaid como imagem usando a API pública mermaid.ink.
    
    Args:
        mermaid_code: Código Mermaid a ser renderizado
        max_width: Largura máxima da imagem no PDF (em pontos) - padrão ~396pts
        max_height: Altura máxima da imagem no PDF (em pontos) - padrão ~684pts
    
    Returns:
        Objeto Image do ReportLab ou None em caso de erro
    """
    try:
        # Limpa o código Mermaid
        cleaned_code = _clean_mermaid_code(mermaid_code)
        
        # Codifica o código Mermaid em base64
        mermaid_bytes = cleaned_code.encode('utf-8')
        mermaid_b64 = base64.urlsafe_b64encode(mermaid_bytes).decode('ascii')
        
        # URL da API do mermaid.ink
        url = f"https://mermaid.ink/img/{mermaid_b64}"
        print(f"[MERMAID] 🌐 Chamando API mermaid.ink...")
        _debug_log(f"URL Mermaid.ink: {url[:100]}...")
        
        # Cria requisição com headers apropriados
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/png,image/*,*/*',
                'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://mermaid.ink/'
            }
        )
        
        # Faz o download da imagem
        with urllib.request.urlopen(req, timeout=15) as response:
            image_data = response.read()
            print(f"[MERMAID] ✅ Imagem recebida: {len(image_data)} bytes")
            _debug_log(f"Imagem recebida: {len(image_data)} bytes")
        
        # Valida se recebeu dados de imagem
        if len(image_data) < 100:
            _debug_log(f"⚠️  Resposta muito pequena ({len(image_data)} bytes), provavelmente erro")
            return None
        
        # Cria um objeto Image do ReportLab
        image_buffer = BytesIO(image_data)
        try:
            # Tenta criar a imagem com largura fixa e altura proporcional
            img = Image(image_buffer, width=max_width, kind='proportional')
            
            # Verifica se a altura ficou maior que o limite
            if img.drawHeight > max_height:
                # Precisa redimensionar novamente pela altura
                ratio = max_height / img.drawHeight
                img.drawWidth = img.drawWidth * ratio
                img.drawHeight = max_height
                _debug_log(f"⚠️  Imagem muito alta, redimensionada para: {img.drawWidth:.0f}x{img.drawHeight:.0f}pts")
            else:
                _debug_log(f"✅ Imagem criada: {img.drawWidth:.0f}x{img.drawHeight:.0f}pts")
                
        except Exception as img_error:
            _debug_log(f"❌ Erro ao criar objeto Image: {img_error}")
            # Tenta abordagem alternativa: deixa o ReportLab calcular tudo
            image_buffer.seek(0)  # Reset do buffer
            img = Image(image_buffer)
            
            # Calcula fator de escala para caber em AMBOS os limites
            width_ratio = max_width / img.drawWidth if img.drawWidth > max_width else 1.0
            height_ratio = max_height / img.drawHeight if img.drawHeight > max_height else 1.0
            
            # Usa o menor fator (mais restritivo) para manter proporções
            scale = min(width_ratio, height_ratio)
            
            if scale < 1.0:
                img.drawWidth = img.drawWidth * scale
                img.drawHeight = img.drawHeight * scale
                _debug_log(f"✅ Imagem redimensionada (escala {scale:.2f}): {img.drawWidth:.0f}x{img.drawHeight:.0f}pts")
            else:
                _debug_log(f"✅ Imagem criada (alternativa): {img.drawWidth:.0f}x{img.drawHeight:.0f}pts")
        
        return img
    except Exception as e:
        print(f"[MERMAID] ❌ ERRO ao renderizar: {type(e).__name__}: {e}")
        _debug_log(f"❌ Erro ao renderizar Mermaid: {type(e).__name__}: {e}")
        _debug_log(f"URL que falhou: {url if 'url' in locals() else 'N/A'}")
        return None


def _apply_text_formatting(text: str) -> str:
    """
    Aplica formatações de markdown para tags HTML do ReportLab.
    - **texto** vira <b>texto</b>
    
    Args:
        text: Texto em markdown
    
    Returns:
        Texto com tags HTML do ReportLab
    """
    # Bold: **texto** -> <b>texto</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    return text


def parse_markdown_to_reportlab(markdown_text: str) -> list:
    """
    Converte markdown para elementos do ReportLab (Platypus).
    ReportLab suporta Unicode completo, então não precisa limpar caracteres especiais.
    
    Returns:
        Lista de elementos Flowable do ReportLab
    """
    styles = getSampleStyleSheet()
    story = []
    
    # Estilos customizados
    styles.add(ParagraphStyle(
        name='CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=TA_CENTER
    ))
    
    styles.add(ParagraphStyle(
        name='CustomHeading2',
        parent=styles['Heading2'],
        fontSize=18,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=12,
        spaceBefore=12
    ))
    
    styles.add(ParagraphStyle(
        name='CustomHeading3',
        parent=styles['Heading3'],
        fontSize=14,
        textColor=colors.HexColor('#34495e'),
        spaceAfter=10,
        spaceBefore=10
    ))
    
    styles.add(ParagraphStyle(
        name='CustomBody',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    ))
    
    styles.add(ParagraphStyle(
        name='CodeBlock',
        parent=styles['Code'],
        fontSize=9,
        leftIndent=20,
        backColor=colors.HexColor('#f5f5f5')
    ))
    
    styles.add(ParagraphStyle(
        name='TableCell',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        wordWrap='CJK'
    ))
    
    styles.add(ParagraphStyle(
        name='TableHeader',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
        textColor=colors.whitesmoke,
        wordWrap='CJK'
    ))
    
    lines = markdown_text.split('\n')
    i = 0
    in_table = False
    table_rows = []
    in_code_block = False
    code_block_type = None  # Para armazenar o tipo do code block (mermaid, python, etc.)
    code_lines = []
    in_list = False
    list_items = []
    
    while i < len(lines):
        line = lines[i]
        
        # Code blocks
        if line.strip().startswith('```'):
            if in_code_block:
                # Fim do bloco de código
                code_text = '\n'.join(code_lines)
                
                # Se for Mermaid, tenta renderizar como imagem
                if code_block_type == 'mermaid':
                    print(f"[MERMAID] 🎨 Tentando renderizar diagrama ({len(code_text)} chars)...")
                    mermaid_img = _render_mermaid_image(code_text)
                    if mermaid_img:
                        # Adiciona a imagem renderizada
                        print(f"[MERMAID] ✅ Imagem renderizada - adicionando ao PDF (dimensões: {mermaid_img.drawWidth:.0f}x{mermaid_img.drawHeight:.0f}pts)")
                        story.append(mermaid_img)
                        story.append(Spacer(1, 0.1*inch))
                    else:
                        print(f"[MERMAID] ⚠️  Falha ao renderizar - usando apenas código")
                    # Adiciona também o código Mermaid abaixo da imagem
                    story.append(Paragraph(f'<font name="Courier">{code_text}</font>', styles['CodeBlock']))
                    story.append(Spacer(1, 0.2*inch))
                else:
                    # Código normal (não Mermaid)
                    story.append(Paragraph(f'<font name="Courier">{code_text}</font>', styles['CodeBlock']))
                    story.append(Spacer(1, 0.2*inch))
                
                code_lines = []
                code_block_type = None
                in_code_block = False
            else:
                # Início do bloco de código
                # Detecta o tipo (mermaid, python, etc.)
                match = re.match(r'^```(\w+)?', line.strip())
                if match and match.group(1):
                    code_block_type = match.group(1).lower()
                else:
                    code_block_type = None
                in_code_block = True
            i += 1
            continue
        
        if in_code_block:
            # Para code blocks ainda não identificados como mermaid,
            # verifica se o conteúdo inicia com sintaxe Mermaid (flowchart, graph, etc.)
            if code_block_type != 'mermaid' and not code_lines:
                # Primeira linha do bloco - verifica se é Mermaid
                if re.match(r'^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitGraph)', line.strip(), re.IGNORECASE):
                    # Detectou sintaxe Mermaid sem identificador explícito
                    code_block_type = 'mermaid'
                    print(f"[MERMAID] 🔍 Auto-detectado código Mermaid: {line.strip()[:60]}...")
            
            code_lines.append(line)
            i += 1
            continue
        
        # Headers
        if line.startswith('# '):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
            story.append(Paragraph(_apply_text_formatting(line[2:]), styles['CustomTitle']))
            
        elif line.startswith('## '):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
            story.append(Spacer(1, 0.2*inch))
            story.append(Paragraph(_apply_text_formatting(line[3:]), styles['CustomHeading2']))
            
        elif line.startswith('### '):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
            story.append(Paragraph(_apply_text_formatting(line[4:]), styles['CustomHeading3']))
        
        # Tables
        elif '|' in line and line.strip().startswith('|'):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
                
            if not in_table:
                in_table = True
                table_rows = []
            
            # Remove pipes das extremidades e split
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            
            # Pula linhas de separação (|---|---|)
            if all(re.match(r'^[-:]+$', cell.strip()) for cell in cells if cell.strip()):
                i += 1
                continue
            
            table_rows.append(cells)
            
            # Verifica se a próxima linha ainda é tabela
            if i + 1 >= len(lines) or '|' not in lines[i + 1]:
                # Renderiza a tabela
                if table_rows:
                    # Calcula largura disponível (A4 = 595 pts, margens = 72*2)
                    available_width = A4[0] - 144  # 451 pts
                    num_cols = len(table_rows[0])
                    col_width = available_width / num_cols
                    col_widths = [col_width] * num_cols
                    
                    # Converte células para Paragraphs para permitir quebra de linha
                    formatted_rows = []
                    for row_idx, row in enumerate(table_rows):
                        formatted_row = []
                        for cell in row:
                            # Aplica formatação de texto (negrito, etc.)
                            cell_text = _apply_text_formatting(cell)
                            # Usa estilo diferente para header vs body
                            if row_idx == 0:
                                formatted_row.append(Paragraph(cell_text, styles['TableHeader']))
                            else:
                                formatted_row.append(Paragraph(cell_text, styles['TableCell']))
                        formatted_rows.append(formatted_row)
                    
                    t = Table(formatted_rows, colWidths=col_widths, repeatRows=1)
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 9),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                        ('TOPPADDING', (0, 0), (-1, -1), 6),
                        ('LEFTPADDING', (0, 0), (-1, -1), 4),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 0.2*inch))
                in_table = False
                table_rows = []
        
        # Lists
        elif line.strip().startswith('- ') or line.strip().startswith('* '):
            list_text = _apply_text_formatting(line.strip()[2:])
            list_items.append(ListItem(Paragraph(list_text, styles['CustomBody']), leftIndent=20))
            in_list = True
            
        elif re.match(r'^\d+\.\s', line.strip()):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
            list_text = _apply_text_formatting(re.sub(r'^\d+\.\s', '', line.strip()))
            story.append(Paragraph(f"• {list_text}", styles['CustomBody']))
            in_list = False
        
        # Empty lines
        elif line.strip() == '':
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
            story.append(Spacer(1, 0.1*inch))
        
        # Normal text
        else:
            if line.strip():
                if in_list and list_items:
                    story.append(ListFlowable(list_items, bulletType='bullet'))
                    list_items = []
                    in_list = False
                story.append(Paragraph(_apply_text_formatting(line.strip()), styles['CustomBody']))
        
        i += 1
    
    # Adiciona lista pendente se houver
    if in_list and list_items:
        story.append(ListFlowable(list_items, bulletType='bullet'))
    
    return story


# Timeout (segundos) para a geração do PDF
_PDF_TIMEOUT = 120


def _build_pdf(markdown_content: str) -> bytes:
    """
    Executa o processamento síncrono e CPU-intensivo do ReportLab.
    Deve ser chamada via asyncio.to_thread() para não bloquear o event loop.

    Returns:
        Bytes do arquivo PDF gerado.
    """
    try:
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )
        story = parse_markdown_to_reportlab(markdown_content)
        doc.build(story)
        pdf_bytes = pdf_buffer.getvalue()
        pdf_buffer.close()
        return pdf_bytes
    except Exception as e:
        # Fallback: PDF mínimo com mensagem de erro
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [
            Paragraph("Documento do Processo AS-IS", styles["Title"]),
            Spacer(1, 0.3 * inch),
            Paragraph("NOTA: Houve um erro ao processar o documento completo.", styles["Normal"]),
            Paragraph(f"Detalhes: {str(e)[:100]}", styles["Normal"]),
        ]
        doc.build(story)
        pdf_bytes = pdf_buffer.getvalue()
        pdf_buffer.close()
        return pdf_bytes


async def generate_pdf_tool(
    markdown_content: str,
    tool_context,
) -> Dict[str, Any]:
    """
    Converte o conteúdo markdown gerado pelo agente em um arquivo PDF usando ReportLab
    e o disponibiliza como artefato para download.
    O processamento CPU-intensivo (ReportLab) é executado em uma thread separada
    para não bloquear o event loop do ADK.

    Args:
        markdown_content: String contendo o conteúdo em formato Markdown.
        tool_context: Contexto da ferramenta para salvar artefatos.

    Returns:
        Dicionário com status da operação.
    """
    if not markdown_content or not markdown_content.strip():
        return {"status": "error", "message": "Conteúdo markdown está vazio"}

    try:
        # Executa o rendering ReportLab em thread separada
        pdf_bytes = await asyncio.wait_for(
            asyncio.to_thread(_build_pdf, markdown_content),
            timeout=_PDF_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": f"Timeout ao gerar PDF após {_PDF_TIMEOUT}s.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar PDF: {str(e)[:200]}"}

    if not pdf_bytes or len(pdf_bytes) == 0:
        return {"status": "error", "message": "PDF gerado está vazio"}

    artifact_part = Part(
        inline_data=Blob(
            data=pdf_bytes,
            mime_type="application/pdf",
        )
    )

    version = await tool_context.save_artifact(
        filename="documento_processo.pdf",
        artifact=artifact_part,
    )

    return {
        "status": "success",
        "message": f"Arquivo 'documento_processo.pdf' (versão {version}) gerado com sucesso e disponível para download.",
        "filename": "documento_processo.pdf",
        "version": version,
        "size_bytes": len(pdf_bytes)
    }
