import asyncio
import re
import base64
import urllib.request
import os
import html as html_utils
from io import BytesIO
from typing import Dict, Any, Optional

from google.genai.types import Part, Blob
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, ListFlowable, ListItem, Image, KeepTogether
from reportlab.lib import colors

try:
    import markdown as markdown_lib
except Exception:  # pragma: no cover - dependencia opcional
    markdown_lib = None

try:
    from weasyprint import HTML
except Exception:  # pragma: no cover - dependencia opcional
    HTML = None


# Debug mode (ativa logs detalhados)
DEBUG_MERMAID = os.getenv('DEBUG_MERMAID', 'false').lower() == 'true'

# Renderização online do Mermaid via API pública mermaid.ink (chamada de rede
# externa, fora do Google). Investigação de 2026-08-06 (agente-processos)
# encontrou essa chamada travando indefinidamente em produção no Vertex AI
# Agent Engine para diagramas maiores — nem o timeout=15 do urlopen nem o
# timeout externo de 120s do pipeline conseguiram interromper (mesmo padrão
# de travamento não-cancelável já visto com Firestore/Postgres nesse
# ambiente). Desabilitado por padrão no deploy.py; continua ligado por
# padrão localmente (MERMAID_RENDER_ENABLED não definido = true).
MERMAID_RENDER_ENABLED = os.getenv('MERMAID_RENDER_ENABLED', 'true').lower() not in ('false', '0', 'no')


def _debug_log(message: str):
    """Imprime log apenas se DEBUG_MERMAID estiver ativo."""
    if DEBUG_MERMAID:
        print(f"[DEBUG] {message}")


def _normalize_markdown_for_mermaid(markdown_text: str) -> str:
    """
    Normaliza markdown para aumentar a taxa de detecção de blocos Mermaid.

    Casos tratados:
    - Conteúdo serializado com "\\n" literal (sem quebras reais de linha).
    - Fences Mermaid inline (abertura/conteúdo/fechamento na mesma linha).

    Retorna sempre blocos Mermaid no formato canônico:
    ```mermaid
    ...
    ```
    """
    if not markdown_text:
        return markdown_text

    normalized = markdown_text.replace('\r\n', '\n').replace('\r', '\n')

    # Se o markdown veio serializado (sem '\n' reais), converte '\\n' em quebra real.
    if '\n' not in normalized and r'\n' in normalized:
        normalized = normalized.replace(r'\n', '\n')

    mermaid_fence_pattern = re.compile(
        r'(?P<fence>`{3,}|~{3,})[ \t]*mermaid(?:[^\n\r]*)\s*'
        r'(?P<body>.*?)'
        r'(?P=fence)',
        re.IGNORECASE | re.DOTALL,
    )

    def _normalize_block(match: re.Match) -> str:
        body = (match.group('body') or '').strip()
        return f"\n```mermaid\n{body}\n```\n"

    return mermaid_fence_pattern.sub(_normalize_block, normalized)


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

    # Se o código já veio multi-linha (uma instrução por linha, formato esperado
    # do prompt do agente), NÃO rodar o colapsa-e-reconstrói agressivo abaixo:
    # a reconstrução via regex só reconhece início de nó com [A-Z], o que destrói
    # IDs em minúsculas (ex.: "inicio", "coleta") gerados pelo nosso prompt.
    original_lines = [ln.strip() for ln in original.split('\n') if ln.strip()]
    if len(original_lines) > 1:
        cleaned = '\n'.join(original_lines)
        _debug_log(f"Código já multi-linha ({len(original_lines)} linhas) — mantido sem reconstrução destrutiva.")
        return cleaned

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
    formatted = re.sub(r'\]\s+([A-Za-z_][\[{(])', r']\n\1', formatted)

    # Substitui ") X[", ") X(", ") X{" por ")\nX[", ")\nX(", ")\nX{"
    formatted = re.sub(r'\)\s+([A-Za-z_][\[{(])', r')\n\1', formatted)

    # Substitui "} X[", "} X(", "} X{" por "}\nX[", "}\nX(", "}\nX{"
    formatted = re.sub(r'\}\s+([A-Za-z_][\[{(])', r'}\n\1', formatted)

    # Substitui "], X ", ") X ", "} X " (referência a nó já definido) por quebra antes do nó
    # Exemplo: "c -- Não --> e e --> f" deve virar "c -- Não --> e\ne --> f"
    formatted = re.sub(r'\]\s+([A-Za-z_])\s+', r']\n\1 ', formatted)
    formatted = re.sub(r'\)\s+([A-Za-z_])\s+', r')\n\1 ', formatted)
    formatted = re.sub(r'\}\s+([A-Za-z_])\s+', r'}\n\1 ', formatted)

    # Separa nós referenciados que ficam juntos: "e e -->" deve virar "e\ne -->"
    # Pattern: nó (letra) + espaço + outro nó + seta
    formatted = re.sub(r'([A-Za-z_])\s+([A-Za-z_]\s*(?:-->|--|==|\.\.>))', r'\1\n\2', formatted)
    
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


# ─── Renderizador offline de fluxograma (sem chamada de rede) ────────────────
# Investigação de 2026-08-06 (agente-processos) confirmou em produção que a
# chamada de rede ao mermaid.ink (ver MERMAID_RENDER_ENABLED acima) trava
# indefinidamente para diagramas maiores, sem nenhum timeout (urlopen,
# asyncio.wait_for, OS-level) conseguir interromper. Este renderizador
# desenha o fluxograma diretamente com primitivas do ReportLab
# (reportlab.graphics.shapes), sem nenhuma chamada de rede — elimina esse
# risco por completo. Suporta o subconjunto de sintaxe Mermaid usado pelo
# nosso próprio prompt (prompts/prompt_agente_gerador_pdf.txt): nós
# retangulares [Texto], arredondados (Texto) e losango de decisão {Texto},
# conectados por "-->" simples ou "-- Label -->" com rótulo.
_NODE_ID_RE = r'[A-Za-z0-9_]+'
_NODE_SHAPE_RE = r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}'

_EDGE_LABELED_PIPE_RE = re.compile(
    rf'^(?P<src>{_NODE_ID_RE})(?P<src_shape>{_NODE_SHAPE_RE})?\s*-->\s*\|(?P<label>[^|]*)\|\s*'
    rf'(?P<dst>{_NODE_ID_RE})(?P<dst_shape>{_NODE_SHAPE_RE})?\s*$'
)
_EDGE_LABELED_DASH_RE = re.compile(
    rf'^(?P<src>{_NODE_ID_RE})(?P<src_shape>{_NODE_SHAPE_RE})?\s*--\s+(?P<label>.+?)\s+-->\s*'
    rf'(?P<dst>{_NODE_ID_RE})(?P<dst_shape>{_NODE_SHAPE_RE})?\s*$'
)
_EDGE_PLAIN_RE = re.compile(
    rf'^(?P<src>{_NODE_ID_RE})(?P<src_shape>{_NODE_SHAPE_RE})?\s*-->\s*'
    rf'(?P<dst>{_NODE_ID_RE})(?P<dst_shape>{_NODE_SHAPE_RE})?\s*$'
)
_NODE_DECL_RE = re.compile(
    rf'^(?P<id>{_NODE_ID_RE})\s*(?P<shape>{_NODE_SHAPE_RE})\s*$'
)


def _mermaid_shape_kind(shape_text: Optional[str]) -> str:
    if not shape_text:
        return 'rect'
    if shape_text.startswith('{'):
        return 'diamond'
    if shape_text.startswith('('):
        return 'round'
    return 'rect'


def _mermaid_shape_label(node_id: str, shape_text: Optional[str]) -> str:
    if not shape_text:
        return node_id
    return shape_text[1:-1].strip() or node_id


def _parse_mermaid_flowchart(mermaid_code: str):
    """Faz o parse do subconjunto de sintaxe Mermaid usado pelo nosso prompt.

    Retorna (nodes, edges, order) onde:
      nodes: dict node_id -> {"label": str, "shape": "rect"|"round"|"diamond"}
      edges: list de (src_id, dst_id, label|None)
      order: lista de node_id na ordem de primeira aparição (usada no layout)
    """
    cleaned = _clean_mermaid_code(mermaid_code)
    lines = [ln.strip() for ln in cleaned.split('\n') if ln.strip()]

    nodes: Dict[str, Dict[str, str]] = {}
    edges = []
    order = []

    def _register(node_id: str, shape_text: Optional[str]):
        if node_id not in nodes:
            nodes[node_id] = {
                "label": _mermaid_shape_label(node_id, shape_text),
                "shape": _mermaid_shape_kind(shape_text),
            }
            order.append(node_id)
        elif shape_text:
            # Nó já visto sem shape explícito antes (referência) — agora define o shape.
            nodes[node_id]["label"] = _mermaid_shape_label(node_id, shape_text)
            nodes[node_id]["shape"] = _mermaid_shape_kind(shape_text)

    in_subgraph = False
    for line in lines:
        low = line.lower()
        if low.startswith(('flowchart', 'graph ')) or low == 'graph':
            continue
        if low.startswith('subgraph'):
            in_subgraph = True
            continue
        if low == 'end':
            in_subgraph = False
            continue
        if line.startswith('%%'):
            continue

        m = _EDGE_LABELED_PIPE_RE.match(line) or _EDGE_LABELED_DASH_RE.match(line) or _EDGE_PLAIN_RE.match(line)
        if m:
            gd = m.groupdict()
            _register(gd['src'], gd.get('src_shape'))
            _register(gd['dst'], gd.get('dst_shape'))
            label = (gd.get('label') or '').strip() or None
            edges.append((gd['src'], gd['dst'], label))
            continue

        m = _NODE_DECL_RE.match(line)
        if m:
            _register(m.group('id'), m.group('shape'))
            continue
        # Linha não reconhecida (ex.: sintaxe não suportada) — ignora silenciosamente.

    return nodes, edges, order


def _layout_flowchart_ranks(nodes: dict, edges: list, order: list) -> Dict[str, int]:
    """Calcula o "rank" (linha/nível) de cada nó via longest-path layering.

    Nós sem aresta de entrada começam no rank 0; cada nó recebe
    max(rank atual, rank_do_predecessor + 1), processado em ordem
    topológica (Kahn). Um fluxograma pode legitimamente ter um ciclo (ex.:
    "reprovado -> volta para o colaborador -> reenvia"): quando a fila
    topológica trava (ciclo), força o próximo nó ainda pendente — na ordem
    de aparição no código — a entrar na fila mesmo com predecessores
    pendentes, "quebrando" o ciclo naquele ponto. O nó forçado mantém
    qualquer rank já propagado por predecessores já processados, e a(s)
    aresta(s) que ainda apontava(m) pra ele são automaticamente tratadas
    como "aresta lateral" mais adiante (rank do destino não é src+1),
    já que só se propaga rank para nós ainda em `remaining`.
    """
    incoming = {nid: [] for nid in nodes}
    outgoing = {nid: [] for nid in nodes}
    for src, dst, _label in edges:
        if src in nodes and dst in nodes and src != dst:
            outgoing[src].append(dst)
            incoming[dst].append(src)

    remaining_in_degree = {nid: len(incoming[nid]) for nid in nodes}
    rank = {nid: 0 for nid in nodes}

    from collections import deque
    remaining = set(nodes.keys())
    queue = deque(nid for nid in order if remaining_in_degree[nid] == 0)

    while remaining:
        if not queue:
            for nid in order:
                if nid in remaining:
                    queue.append(nid)
                    break
        nid = queue.popleft()
        if nid not in remaining:
            continue
        remaining.discard(nid)
        for nxt in outgoing[nid]:
            if nxt in remaining:
                rank[nxt] = max(rank[nxt], rank[nid] + 1)
                remaining_in_degree[nxt] -= 1
                if remaining_in_degree[nxt] <= 0:
                    queue.append(nxt)

    return rank


def _wrap_text_lines(text: str, font_name: str, font_size: float, max_width: float, max_lines: int = 3) -> list:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    words = text.split()
    if not words:
        return ['']
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) >= max_lines - 1:
            break
    lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines:
        # Verifica se sobrou texto não incluído — trunca a última linha com "..."
        consumed = sum(len(l) for l in lines)
        if consumed < len(text.replace(' ', '')):
            last = lines[-1]
            while stringWidth(last + '...', font_name, font_size) > max_width and len(last) > 1:
                last = last[:-1]
            lines[-1] = last + '...'
    return lines


def _draw_flowchart_offline(mermaid_code: str, max_width: float = 5.5 * inch, max_height: float = 9.5 * inch) -> Optional['Drawing']:
    """Desenha o fluxograma Mermaid diretamente com primitivas ReportLab,
    sem nenhuma chamada de rede. Retorna None se o código não puder ser
    interpretado (ex.: sintaxe não suportada) — quem chama deve cair no
    fallback de texto puro nesse caso.
    """
    try:
        from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group
    except Exception:
        return None

    try:
        nodes, edges, order = _parse_mermaid_flowchart(mermaid_code)
        if not nodes:
            return None

        rank = _layout_flowchart_ranks(nodes, edges, order)

        rows: Dict[int, list] = {}
        for nid in order:
            rows.setdefault(rank.get(nid, 0), []).append(nid)

        FONT_NAME = 'Helvetica'
        FONT_SIZE = 8
        BOX_WIDTH = 170
        BOX_PADDING = 8
        LINE_HEIGHT = 10
        GAP_X = 22
        GAP_Y = 34
        MARGIN = 10

        box_dims = {}
        for nid, info in nodes.items():
            usable_w = BOX_WIDTH - 2 * BOX_PADDING
            lines = _wrap_text_lines(info['label'], FONT_NAME, FONT_SIZE, usable_w)
            h = max(34, len(lines) * LINE_HEIGHT + 2 * BOX_PADDING)
            box_dims[nid] = {'lines': lines, 'w': BOX_WIDTH, 'h': h}

        sorted_ranks = sorted(rows.keys())
        row_heights = {r: max(box_dims[nid]['h'] for nid in rows[r]) for r in sorted_ranks}
        row_width = {r: len(rows[r]) * BOX_WIDTH + (len(rows[r]) - 1) * GAP_X for r in sorted_ranks}
        content_width = max(row_width.values()) if row_width else BOX_WIDTH

        # Arestas que não vão de um rank pro seguinte (voltas/loops, ex.: uma
        # reprovação que volta pra uma etapa anterior) não podem ser desenhadas
        # como linha reta — cruzariam por cima das caixas do meio. Reserva uma
        # faixa lateral à direita pra rotear essas arestas por fora.
        def _is_side_edge(src, dst):
            if src not in nodes or dst not in nodes:
                return False
            return rank.get(dst, 0) != rank.get(src, 0) + 1

        # Cada aresta lateral ganha sua própria coluna, deslocada mais pra fora
        # que a anterior, pra não colidir quando há mais de uma volta no mesmo
        # diagrama (ex.: duas reprovações que retornam a etapas diferentes).
        side_edge_loop_x: Dict[tuple, float] = {}
        LOOP_STEP = 16
        LOOP_START = 22
        for _src, _dst, _lbl in edges:
            if _is_side_edge(_src, _dst):
                key = (_src, _dst, _lbl)
                side_edge_loop_x[key] = content_width + LOOP_START + LOOP_STEP * len(side_edge_loop_x)
        LOOP_MARGIN = (LOOP_START + LOOP_STEP * len(side_edge_loop_x) + 15) if side_edge_loop_x else 0
        canvas_width = content_width + LOOP_MARGIN

        positions = {}
        y_cursor = MARGIN
        # Desenha de cima pra baixo, mas em coordenadas ReportLab (origem embaixo à
        # esquerda) o rank 0 deve ficar no topo — por isso preenche de trás pra
        # frente ao final (inverte depois de calcular a altura total).
        row_y = {}
        for r in sorted_ranks:
            row_y[r] = y_cursor
            y_cursor += row_heights[r] + GAP_Y
        total_height = y_cursor - GAP_Y + MARGIN

        for r in sorted_ranks:
            n = len(rows[r])
            total_w = row_width[r]
            # Centraliza dentro da largura de conteúdo (sem a faixa lateral de loop),
            # mantendo as caixas à esquerda e a faixa reservada livre à direita.
            start_x = (content_width - total_w) / 2
            x_cursor = start_x
            # y invertido: rank 0 no topo do desenho final
            top_y = total_height - row_y[r] - row_heights[r]
            for nid in rows[r]:
                w = box_dims[nid]['w']
                h = box_dims[nid]['h']
                positions[nid] = {
                    'x': x_cursor, 'y': top_y, 'w': w, 'h': h,
                    'cx': x_cursor + w / 2, 'cy': top_y + h / 2,
                }
                x_cursor += w + GAP_X

        canvas_height = total_height

        shapes = []
        NODE_FILL = {
            'rect': '#E8F0FE', 'round': '#E6F4EA', 'diamond': '#FEF7E0',
        }
        NODE_STROKE = {
            'rect': '#4285F4', 'round': '#34A853', 'diamond': '#F9AB00',
        }

        import math
        EDGE_COLOR = colors.HexColor('#5F6368')

        def _add_arrowhead(x2, y2, ang):
            arrow_len, arrow_w = 7, 4
            ax = x2 - arrow_len * math.cos(ang)
            ay = y2 - arrow_len * math.sin(ang)
            perp = ang + math.pi / 2
            p_left = (ax + arrow_w * math.cos(perp), ay + arrow_w * math.sin(perp))
            p_right = (ax - arrow_w * math.cos(perp), ay - arrow_w * math.sin(perp))
            shapes.append(Polygon(
                points=[x2, y2, p_left[0], p_left[1], p_right[0], p_right[1]],
                fillColor=EDGE_COLOR, strokeColor=None,
            ))

        def _add_label(mx, my, label):
            if not label:
                return
            label_w = min(len(label) * FONT_SIZE * 0.6 + 6, 90)
            shapes.append(Rect(mx - label_w / 2, my - 6, label_w, 12,
                                fillColor=colors.white, strokeColor=None))
            shapes.append(String(mx, my - 3, label, fontName=FONT_NAME,
                                  fontSize=FONT_SIZE - 1, textAnchor='middle',
                                  fillColor=EDGE_COLOR))

        # Arestas primeiro (para ficarem atrás das caixas)
        for src, dst, label in edges:
            if src not in positions or dst not in positions:
                continue
            p1, p2 = positions[src], positions[dst]

            if _is_side_edge(src, dst):
                # Aresta que não vai pro próximo rank em sequência (ex.: uma volta/loop,
                # como reprovação que retorna a uma etapa anterior) — roteia por uma
                # alça pela faixa lateral direita reservada, em vez de linha reta que
                # cruzaria por cima das caixas do meio. Cada aresta lateral usa sua
                # própria coluna (side_edge_loop_x) pra não colidir com outras.
                loop_x = side_edge_loop_x.get((src, dst, label), canvas_width - 8)
                x1, y1 = p1['x'] + p1['w'], p1['cy']
                x2, y2 = p2['x'] + p2['w'], p2['cy']
                shapes.append(Line(x1, y1, loop_x, y1, strokeColor=EDGE_COLOR, strokeWidth=1.2))
                shapes.append(Line(loop_x, y1, loop_x, y2, strokeColor=EDGE_COLOR, strokeWidth=1.2))
                shapes.append(Line(loop_x, y2, x2, y2, strokeColor=EDGE_COLOR, strokeWidth=1.2))
                _add_arrowhead(x2, y2, math.pi)  # aponta pra esquerda, de volta pro nó
                _add_label(loop_x + 4, (y1 + y2) / 2, label)
                continue

            if p2['cy'] < p1['cy']:
                x1, y1 = p1['cx'], p1['y']
                x2, y2 = p2['cx'], p2['y'] + p2['h']
            elif p2['cy'] > p1['cy']:
                x1, y1 = p1['cx'], p1['y'] + p1['h']
                x2, y2 = p2['cx'], p2['y']
            else:
                x1, y1 = p1['x'] + p1['w'], p1['cy']
                x2, y2 = p2['x'], p2['cy']

            shapes.append(Line(x1, y1, x2, y2, strokeColor=EDGE_COLOR, strokeWidth=1.2))
            _add_arrowhead(x2, y2, math.atan2(y2 - y1, x2 - x1))
            _add_label((x1 + x2) / 2, (y1 + y2) / 2, label)

        # Caixas dos nós
        for nid, pos in positions.items():
            info = nodes[nid]
            dims = box_dims[nid]
            kind = info['shape']
            fill = colors.HexColor(NODE_FILL.get(kind, '#E8F0FE'))
            stroke = colors.HexColor(NODE_STROKE.get(kind, '#4285F4'))

            if kind == 'diamond':
                cx, cy = pos['cx'], pos['cy']
                hw, hh = pos['w'] / 2, pos['h'] / 2
                shapes.append(Polygon(
                    points=[cx, cy + hh, cx + hw, cy, cx, cy - hh, cx - hw, cy],
                    fillColor=fill, strokeColor=stroke, strokeWidth=1,
                ))
            elif kind == 'round':
                shapes.append(Rect(pos['x'], pos['y'], pos['w'], pos['h'],
                                    rx=pos['h'] / 2, ry=pos['h'] / 2,
                                    fillColor=fill, strokeColor=stroke, strokeWidth=1))
            else:
                shapes.append(Rect(pos['x'], pos['y'], pos['w'], pos['h'],
                                    fillColor=fill, strokeColor=stroke, strokeWidth=1))

            lines = dims['lines']
            text_y = pos['cy'] + (len(lines) - 1) * LINE_HEIGHT / 2
            for ln in lines:
                shapes.append(String(pos['cx'], text_y - FONT_SIZE / 2.8, ln,
                                      fontName=FONT_NAME, fontSize=FONT_SIZE,
                                      textAnchor='middle', fillColor=colors.HexColor('#202124')))
                text_y -= LINE_HEIGHT

        scale = min(max_width / canvas_width, max_height / canvas_height, 1.0)
        final_w = canvas_width * scale
        final_h = canvas_height * scale

        drawing = Drawing(final_w, final_h)
        if scale < 1.0:
            group = Group(*shapes)
            group.transform = (scale, 0, 0, scale, 0, 0)
            drawing.add(group)
        else:
            for shp in shapes:
                drawing.add(shp)

        # Centraliza o diagrama na largura útil da página (por padrão os
        # Flowables do ReportLab alinham à esquerda, deixando um vão em
        # branco grande à direita para diagramas mais estreitos).
        drawing.hAlign = 'CENTER'

        return drawing
    except Exception as e:
        print(f"[MERMAID] Renderizador offline falhou: {type(e).__name__}: {e}")
        return None


def _render_mermaid_image(mermaid_code: str, max_width: float = 5.5 * inch, max_height: float = 9.5 * inch):
    """
    Renderiza um diagrama Mermaid para o PDF. Tenta primeiro o renderizador
    offline (sem rede, ver _draw_flowchart_offline) — cobre o subconjunto de
    sintaxe usado pelo nosso prompt e é o caminho usado em produção. Só cai
    para a API online mermaid.ink se o offline não conseguir interpretar o
    código E MERMAID_RENDER_ENABLED estiver ligado (desligado em produção).

    Returns:
        Um Drawing (offline) ou Image (online) do ReportLab, ou None se
        nenhum dos dois conseguir renderizar.
    """
    drawing = _draw_flowchart_offline(mermaid_code, max_width, max_height)
    if drawing is not None:
        return drawing

    if not MERMAID_RENDER_ENABLED:
        return None

    return _render_mermaid_image_online(mermaid_code, max_width, max_height)


def _render_mermaid_image_online(mermaid_code: str, max_width: float = 5.5 * inch, max_height: float = 9.5 * inch) -> Optional[Image]:
    """
    Renderiza um diagrama Mermaid como imagem usando a API pública mermaid.ink.
    Fallback usado apenas quando o renderizador offline não consegue
    interpretar o código E MERMAID_RENDER_ENABLED está ligado.

    Args:
        mermaid_code: Código Mermaid a ser renderizado
        max_width: Largura máxima da imagem no PDF (em pontos) - padrão ~396pts
        max_height: Altura máxima da imagem no PDF (em pontos) - padrão ~684pts

    Returns:
        Objeto Image do ReportLab ou None em caso de erro
    """
    try:
        image_data = _fetch_mermaid_png_bytes(mermaid_code)
        if not image_data:
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
        return None


def _fetch_mermaid_png_bytes(mermaid_code: str) -> Optional[bytes]:
    """Obtém bytes PNG do Mermaid via mermaid.ink para reutilização em ReportLab e HTML."""
    if not MERMAID_RENDER_ENABLED:
        print("[MERMAID] Renderização online desabilitada (MERMAID_RENDER_ENABLED=false) — usando apenas código.")
        return None

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

    return image_data


def _embed_mermaid_blocks_as_html_images(markdown_content: str) -> str:
    """Substitui blocos ```mermaid por imagens inline (data URI) no markdown."""
    if not markdown_content:
        return markdown_content

    markdown_content = _normalize_markdown_for_mermaid(markdown_content)

    # Aceita fences ``` ou ~~~, com indentacao opcional e final de linha LF/CRLF.
    # Isso evita perder Mermaid quando o markdown vem com variacoes de formato.
    pattern = re.compile(
        r"(^|\n)[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*mermaid(?:[^\r\n]*)[\r\n]+"
        r"(?P<body>.*?)"
        r"(?:[\r\n]+[ \t]*(?P=fence)[ \t]*(?=\r?\n|$))",
        re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match) -> str:
        mermaid_code = (match.group("body") or "").strip()
        try:
            image_data = _fetch_mermaid_png_bytes(mermaid_code)
        except Exception as exc:
            print(f"[MERMAID] ⚠️ Falha ao gerar imagem para HTML: {type(exc).__name__}: {exc}")
            return match.group(0)

        if not image_data:
            return match.group(0)

        image_b64 = base64.b64encode(image_data).decode("ascii")
        escaped_code = html_utils.escape(mermaid_code)
        return (
            (match.group(1) or "") +
            "<div class=\"mermaid-diagram\">"
            f"<img src=\"data:image/png;base64,{image_b64}\" alt=\"Diagrama Mermaid\" />"
            f"<pre class=\"mermaid-source\">{escaped_code}</pre>"
            "</div>"
        )

    return pattern.sub(_replace, markdown_content)


def _apply_text_formatting(text: str) -> str:
    """
    Aplica formatações de markdown para tags HTML do ReportLab.
    - **texto** vira <b>texto</b>
    
    Args:
        text: Texto em markdown
    
    Returns:
        Texto com tags HTML do ReportLab
    """
    # ReportLab não aceita <br> cru; converte para quebra de linha real.
    text = text.replace('<br>', '\n')

    # Bold: **texto** -> <b>texto</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    return text


def _is_table_divider_line(line: str) -> bool:
    """Detecta linha separadora de tabela Markdown (ex: |---|:---:|---|)."""
    stripped = line.strip()
    if not stripped:
        return False
    normalized = stripped.strip('|')
    parts = [p.strip() for p in normalized.split('|')]
    if not parts:
        return False
    return all(bool(p) and re.fullmatch(r':?-{3,}:?', p) for p in parts)


def _split_table_cells(line: str) -> list[str]:
    """Quebra uma linha de tabela em células, tolerando ausência de pipes nas pontas."""
    cleaned = line.strip().strip('|')
    return [cell.strip().replace('<br>', '\n') for cell in cleaned.split('|')]


def _looks_like_table_row(line: str) -> bool:
    """Heurística para linha de tabela: possui ao menos um pipe e não é vazia."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _calc_table_col_widths(table_rows: list[list[str]], available_width: float) -> list[float]:
    """Calcula larguras proporcionais das colunas com limites para evitar overflow."""
    num_cols = max((len(r) for r in table_rows), default=1)
    min_w = 55.0
    max_w = 170.0

    col_scores = [1.0] * num_cols
    for row in table_rows:
        for idx in range(num_cols):
            cell = row[idx] if idx < len(row) else ''
            # Score simples baseado no maior token e tamanho total para favorecer colunas textuais.
            max_token = max((len(tok) for tok in re.split(r'\s+', cell) if tok), default=1)
            score = 0.6 * min(max_token, 30) + 0.4 * min(len(cell), 80)
            col_scores[idx] = max(col_scores[idx], score)

    total = sum(col_scores) or float(num_cols)
    widths = [max(min_w, min(max_w, available_width * (s / total))) for s in col_scores]

    current = sum(widths)
    if current > available_width:
        factor = available_width / current
        widths = [max(min_w, w * factor) for w in widths]

    overflow = sum(widths) - available_width
    if overflow > 0:
        idx = max(range(len(widths)), key=lambda i: widths[i])
        widths[idx] = max(min_w, widths[idx] - overflow)

    return widths


def _normalize_table_rows(table_rows: list[list[str]]) -> list[list[str]]:
    """Normaliza cardinalidade de colunas, mesclando excedentes na última coluna."""
    if not table_rows:
        return []
    num_cols = max(len(r) for r in table_rows)
    normalized = []
    for row in table_rows:
        if len(row) == num_cols:
            normalized.append(row)
            continue
        if len(row) > num_cols:
            merged_last = ' | '.join(row[num_cols - 1:])
            normalized.append(row[:num_cols - 1] + [merged_last])
            continue
        normalized.append(row + [''] * (num_cols - len(row)))
    return normalized


def _build_table_flowable(table_rows: list[list[str]], styles) -> Table:
    """Cria Table do ReportLab com quebra de linha e largura ajustada à página."""
    normalized_rows = _normalize_table_rows(table_rows)
    available_width = A4[0] - 144  # página A4 com margens laterais de 72pt
    col_widths = _calc_table_col_widths(normalized_rows, available_width)

    formatted_rows = []
    for row_idx, row in enumerate(normalized_rows):
        formatted_row = []
        for cell in row:
            cell_text = _apply_text_formatting(cell)
            style = styles['TableHeader'] if row_idx == 0 else styles['TableCell']
            formatted_row.append(Paragraph(cell_text, style))
        formatted_rows.append(formatted_row)

    table = Table(
        formatted_rows,
        colWidths=col_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    return table


_HEADING_STYLE_NAMES = ('CustomHeading2', 'CustomHeading3', 'CustomTitle')


def _pop_preceding_heading(story: list) -> list:
    """Remove e retorna o título de seção (+ eventuais Spacers de linhas em
    branco) do fim de `story`, se houver um logo antes da posição atual.

    Usado para agrupar (via KeepTogether) um título com o flowable grande
    que vem em seguida (tabela, diagrama Mermaid) — sem isso, se o
    flowable não couber no resto da página atual, ele pula sozinho pra
    próxima página, deixando o título "órfão" seguido de um vão em branco.
    """
    j = len(story) - 1
    while j >= 0 and isinstance(story[j], Spacer):
        j -= 1
    if j >= 0 and isinstance(story[j], Paragraph) and getattr(story[j].style, 'name', '') in _HEADING_STYLE_NAMES:
        items = story[j:]
        del story[j:]
        return items
    return []


def parse_markdown_to_reportlab(markdown_text: str) -> list:
    """
    Converte markdown para elementos do ReportLab (Platypus).
    ReportLab suporta Unicode completo, então não precisa limpar caracteres especiais.

    O markdown que chega pode vir com headings coladas à esquerda sem
    quebras de linha (ex: "# Título ## Subtítulo texto"). Nesses casos a
    parser original tratava tudo como um único parágrafo/heading gigante,
    eventualmente causando exceções do ReportLab por texto muito longo.

    Por isso, logo de início normalizamos o texto:
      * Inserimos uma quebra de linha antes de qualquer marcador de heading
        (#, ##, ###, etc.) que não esteja no começo de uma linha.
      * Também garantimos que todas as linhas terminem com '\n' para que o
        split abaixo funcione corretamente.

    Returns:
        Lista de elementos Flowable do ReportLab
    """
    # Normaliza headings que aparecem no meio de linhas.
    # Ex: "...Câmbio ##1. CONTEXTO..." -> "...Câmbio\n##1. CONTEXTO..."
    # Evita quebrar casos como "C#" exigindo que o char anterior nao seja alfanumerico.
    #
    # IMPORTANTE: nunca aplica em linhas de tabela (contêm '|'). Células de tabela
    # frequentemente têm texto livre com '#' literal (ex.: "Problema #2"), e quebrar
    # a linha ali corrompe a tabela inteira — o restante da linha (incluindo os
    # demais pipes) vira texto de heading gigante.
    def _split_embedded_headings(text: str) -> str:
        fixed_lines = []
        for line in text.split('\n'):
            if '|' in line:
                fixed_lines.append(line)
            else:
                fixed_lines.append(re.sub(r'(?<!\A)(?<!\w)(#{1,6})(?=\s*\S)', r'\n\1', line))
        return '\n'.join(fixed_lines)

    markdown_text = _split_embedded_headings(markdown_text)
    
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
                    mermaid_img = _render_mermaid_image(code_text, max_height=8.5 * inch)
                    if mermaid_img:
                        # Adiciona a imagem renderizada (Drawing offline usa .width/.height, Image online usa .drawWidth/.drawHeight)
                        _w = getattr(mermaid_img, 'drawWidth', getattr(mermaid_img, 'width', 0))
                        _h = getattr(mermaid_img, 'drawHeight', getattr(mermaid_img, 'height', 0))
                        print(f"[MERMAID] ✅ Diagrama renderizado - adicionando ao PDF (dimensões: {_w:.0f}x{_h:.0f}pts)")
                        # Mantém o título da seção junto com o diagrama (ver _pop_preceding_heading).
                        keep_items = _pop_preceding_heading(story) + [mermaid_img]
                        story.append(KeepTogether(keep_items))
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
        
        # Linhas separadoras "soltas" (ex: '#' ou '##' sozinho, ou '---'/'***'/'___'
        # como divisor temático) não têm texto para virar heading nem tabela — sem
        # este filtro, caem no fallback de parágrafo comum e o marcador literal
        # (ex: um "#" solto) aparece impresso no PDF.
        stripped_line = line.strip()
        if re.fullmatch(r'#{1,6}', stripped_line) or re.fullmatch(r'[-*_]{3,}', stripped_line):
            i += 1
            continue

        # Headers (níveis 1-6, com ou sem espaço após '#')
        heading_match = re.match(r'^\s*(#{1,6})\s*(.+?)\s*$', line)
        if heading_match:
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False
            level = len(heading_match.group(1))
            heading_text = _apply_text_formatting(heading_match.group(2))
            if level == 1:
                story.append(Paragraph(heading_text, styles['CustomTitle']))
            elif level == 2:
                story.append(Spacer(1, 0.2*inch))
                story.append(Paragraph(heading_text, styles['CustomHeading2']))
            else:
                story.append(Paragraph(heading_text, styles['CustomHeading3']))
        
        # Tables
        elif _looks_like_table_row(line):
            if in_list and list_items:
                story.append(ListFlowable(list_items, bulletType='bullet'))
                list_items = []
                in_list = False

            if not in_table:
                # Só inicia bloco de tabela se há indicador claro de tabela.
                next_line = lines[i + 1] if i + 1 < len(lines) else ''
                has_header_separator = _is_table_divider_line(next_line)
                if not has_header_separator and not line.strip().startswith('|'):
                    # Texto comum com pipe (não tabela)
                    story.append(Paragraph(_apply_text_formatting(line.strip()), styles['CustomBody']))
                    i += 1
                    continue
                in_table = True
                table_rows = []

            # Linha separadora de header: ignora
            if _is_table_divider_line(line):
                i += 1
                continue

            if _looks_like_table_row(line):
                table_rows.append(_split_table_cells(line))
                # Verifica linhas de continuação da última célula.
                # Isso evita que conteúdo multiline de célula "escape" como lista.
                while i + 1 < len(lines):
                    nxt = lines[i + 1]
                    nxt_stripped = nxt.strip()
                    if not nxt_stripped:
                        break
                    if _looks_like_table_row(nxt) or _is_table_divider_line(nxt):
                        break
                    if re.match(r'^(#{1,6})\s*', nxt_stripped) or nxt_stripped.startswith('```'):
                        break
                    if table_rows and table_rows[-1]:
                        table_rows[-1][-1] = (table_rows[-1][-1] + '\n' + nxt_stripped).strip()
                        i += 1
                        continue
                    break

            # Fecha tabela quando a próxima linha não aparenta pertencer a tabela.
            next_line = lines[i + 1] if i + 1 < len(lines) else ''
            if i + 1 >= len(lines) or (not _looks_like_table_row(next_line) and not _is_table_divider_line(next_line)):
                if table_rows:
                    t = _build_table_flowable(table_rows, styles)
                    # Mantém o título da seção junto com a tabela (ver _pop_preceding_heading).
                    keep_items = _pop_preceding_heading(story) + [t]
                    story.append(KeepTogether(keep_items))
                    story.append(Spacer(1, 0.2 * inch))
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


def _build_html_document(markdown_content: str) -> str:
    """Converte markdown em HTML completo para renderizacao via WeasyPrint."""
    if markdown_lib is None:
        raise RuntimeError("Biblioteca 'markdown' nao instalada")

    markdown_with_images = _embed_mermaid_blocks_as_html_images(markdown_content or "")

    body_html = markdown_lib.markdown(
        markdown_with_images,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="xhtml1",
    )

    return f"""
<!doctype html>
<html lang=\"pt-BR\">
<head>
  <meta charset=\"utf-8\" />
  <style>
    @page {{ size: A4; margin: 20mm; }}
    body {{ font-family: Arial, Helvetica, sans-serif; font-size: 11px; color: #1a1a1a; line-height: 1.4; }}
    h1 {{ font-size: 24px; margin: 0 0 16px 0; text-align: center; }}
    h2 {{ font-size: 18px; margin: 16px 0 8px 0; color: #2c3e50; }}
    h3, h4, h5, h6 {{ font-size: 14px; margin: 12px 0 6px 0; color: #34495e; }}
    p {{ margin: 6px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0; table-layout: fixed; }}
    th, td {{ border: 1px solid #333; padding: 6px; vertical-align: top; word-wrap: break-word; white-space: pre-wrap; }}
    th {{ background: #efefef; }}
    ul, ol {{ margin: 6px 0 6px 20px; }}
    code {{ font-family: "Courier New", monospace; }}
    pre {{ background: #f5f5f5; border: 1px solid #ddd; padding: 8px; overflow-x: auto; white-space: pre-wrap; }}
        .mermaid-diagram {{ margin: 12px 0; text-align: center; page-break-inside: avoid; }}
        .mermaid-diagram img {{
            display: block;
            margin: 0 auto;
            width: auto;
            height: auto;
            max-width: 100%;
            max-height: 150mm;
            object-fit: contain;
            border: 1px solid #ddd;
        }}
        .mermaid-source {{ text-align: left; font-size: 9px; margin-top: 6px; }}
  </style>
</head>
<body>
{body_html}
</body>
</html>
""".strip()


def _build_pdf_from_html(markdown_content: str) -> bytes:
    """Gera PDF via pipeline Markdown -> HTML -> PDF (WeasyPrint)."""
    if HTML is None:
        raise RuntimeError("Biblioteca 'weasyprint' nao instalada")

    html = _build_html_document(markdown_content)
    pdf_bytes = HTML(string=html).write_pdf()
    if not pdf_bytes:
        raise RuntimeError("WeasyPrint retornou PDF vazio")
    return pdf_bytes


def _sanitize_for_reportlab(text: str) -> str:
    """Remove caracteres fora do WinAnsiEncoding (cp1252).

    As fontes padrão do ReportLab (Helvetica etc., via getSampleStyleSheet())
    usam WinAnsiEncoding e não suportam emoji/Unicode fora do Latin-1
    estendido — texto com esses caracteres quebra doc.build() com
    UnicodeEncodeError ('charmap' codec). Acentuação PT-BR é preservada
    (está toda dentro do cp1252); apenas emoji e símbolos exóticos são removidos.
    """
    return text.encode("cp1252", errors="ignore").decode("cp1252")


def _build_pdf(markdown_content: str) -> bytes:
    """
    Executa o processamento síncrono e CPU-intensivo do ReportLab.
    Deve ser chamada via asyncio.to_thread() para não bloquear o event loop.

    Returns:
        Bytes do arquivo PDF gerado.
    """
    markdown_content = _normalize_markdown_for_mermaid(markdown_content or "")

    renderer = os.getenv("PDF_RENDERER", "auto").strip().lower()
    # Novo pipeline para teste: Markdown -> HTML -> PDF.
    # Em modo "auto", tenta HTML primeiro e faz fallback para ReportLab.
    if renderer in {"auto", "html"}:
        try:
            return _build_pdf_from_html(markdown_content)
        except Exception as html_err:
            if renderer == "html":
                # Mesmo em modo html, mantemos fallback para nao quebrar o pipeline.
                print(f"[PDF] Falha no renderizador HTML, usando fallback ReportLab: {html_err}")

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
        story = parse_markdown_to_reportlab(_sanitize_for_reportlab(markdown_content))
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
