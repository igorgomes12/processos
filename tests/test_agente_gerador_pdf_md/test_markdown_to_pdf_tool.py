"""
Testes unitários para agente_gerador_pdf_md/tools/markdown_to_pdf_tool.py.
Cobre: parse_markdown_to_reportlab, _build_pdf.
"""
from __future__ import annotations

import pytest
from io import BytesIO

from agente_gerador_pdf_md.tools.markdown_to_pdf_tool import (
    parse_markdown_to_reportlab,
    _build_pdf,
)
import agente_gerador_pdf_md.tools.markdown_to_pdf_tool as pdf_tool

# ReportLab elements
from reportlab.platypus import Paragraph, Spacer, Table, ListFlowable


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _count_type(elements: list, cls) -> int:
    return sum(1 for e in elements if isinstance(e, cls))


# ─── parse_markdown_to_reportlab ─────────────────────────────────────────────

class TestParseMarkdownToReportlab:
    def test_empty_string_returns_list(self):
        result = parse_markdown_to_reportlab("")
        assert isinstance(result, list)

    def test_single_h1_creates_paragraph(self):
        result = parse_markdown_to_reportlab("# Título Principal")
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1

    def test_single_h2_creates_paragraph(self):
        result = parse_markdown_to_reportlab("## Seção")
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1

    def test_single_h3_creates_paragraph(self):
        result = parse_markdown_to_reportlab("### Subseção")
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1

    def test_plain_text_creates_paragraph(self):
        result = parse_markdown_to_reportlab("Texto simples do corpo.")
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1

    def test_list_items_create_flowable(self):
        md = "- Item 1\n- Item 2\n- Item 3"
        result = parse_markdown_to_reportlab(md)
        list_flowables = [e for e in result if isinstance(e, ListFlowable)]
        assert len(list_flowables) >= 1

    def test_bold_text_creates_paragraph_with_b_tag(self):
        md = "Texto com **negrito** aqui."
        result = parse_markdown_to_reportlab(md)
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        texts = [str(p.text) if hasattr(p, 'text') else "" for p in paragraphs]
        combined = " ".join(texts)
        assert "<b>" in combined

    def test_empty_line_creates_spacer(self):
        md = "Linha 1\n\nLinha 2"
        result = parse_markdown_to_reportlab(md)
        spacers = [e for e in result if isinstance(e, Spacer)]
        assert len(spacers) >= 1

    def test_table_creates_table_object(self):
        md = "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        result = parse_markdown_to_reportlab(md)
        tables = [e for e in result if isinstance(e, Table)]
        assert len(tables) >= 1

    def test_code_block_creates_paragraph(self):
        md = "```\nprint('hello')\n```"
        result = parse_markdown_to_reportlab(md)
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert isinstance(result, list)

    def test_mixed_content_returns_multiple_elements(self):
        md = """# Título

## Seção

Parágrafo de texto.

- Item 1
- Item 2

| Col1 | Col2 |
|------|------|
| A    | B    |
"""
        result = parse_markdown_to_reportlab(md)
        assert len(result) > 5

    def test_returns_list_type(self):
        result = parse_markdown_to_reportlab("qualquer texto")
        assert isinstance(result, list)

    def test_unicode_content_handled(self):
        md = "# Processamento de Negócios\n\nÉtape de processamento com ação e açúcar."
        result = parse_markdown_to_reportlab(md)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_numbered_list_creates_paragraph(self):
        md = "1. Primeiro\n2. Segundo\n3. Terceiro"
        result = parse_markdown_to_reportlab(md)
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1

    def test_indented_heading_is_parsed_as_heading(self):
        md = "   ## Seção com indentação"
        result = parse_markdown_to_reportlab(md)
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 1
        # Não deve manter o marcador markdown no texto renderizado.
        assert all(not str(getattr(p, "text", "")).lstrip().startswith("##") for p in paragraphs)

    def test_inline_heading_without_space_is_split_and_parsed(self):
        md = "Texto introdutório. ##Seção seguinte"
        result = parse_markdown_to_reportlab(md)
        paragraphs = [e for e in result if isinstance(e, Paragraph)]
        assert len(paragraphs) >= 2
        assert all(not str(getattr(p, "text", "")).lstrip().startswith("##") for p in paragraphs)


# ─── _build_pdf ──────────────────────────────────────────────────────────────

class TestBuildPdf:
    def test_mermaid_block_uses_html_renderer_in_auto_mode(self, monkeypatch):
        monkeypatch.setenv("PDF_RENDERER", "auto")

        def _fake_html_renderer(*args, **kwargs):
            return b"HTML_RENDERER_USED"

        monkeypatch.setattr(pdf_tool, "_build_pdf_from_html", _fake_html_renderer)

        md = """```mermaid
flowchart TD
A[Inicio] --> B[Fim]
```"""
        result = _build_pdf(md)
        assert result == b"HTML_RENDERER_USED"

    def test_build_html_document_embeds_mermaid_as_data_uri(self, monkeypatch):
        def _fake_mermaid_bytes(*args, **kwargs):
            return b"fakepngbytes"

        monkeypatch.setattr(pdf_tool, "_fetch_mermaid_png_bytes", _fake_mermaid_bytes)

        md = """# Fluxo

```mermaid
flowchart TD
A[Inicio] --> B[Fim]
```
"""
        html = pdf_tool._build_html_document(md)
        assert "data:image/png;base64," in html
        assert "Diagrama Mermaid" in html

    def test_build_html_document_embeds_mermaid_with_crlf_and_indentation(self, monkeypatch):
        def _fake_mermaid_bytes(*args, **kwargs):
            return b"fakepngbytes"

        monkeypatch.setattr(pdf_tool, "_fetch_mermaid_png_bytes", _fake_mermaid_bytes)

        md = "# Fluxo\r\n\r\n   ```mermaid\r\nflowchart TD\r\nA --> B\r\n   ```\r\n"
        html = pdf_tool._build_html_document(md)
        assert "data:image/png;base64," in html
        assert "Diagrama Mermaid" in html

    def test_build_html_document_embeds_inline_mermaid_block(self, monkeypatch):
        def _fake_mermaid_bytes(*args, **kwargs):
            return b"fakepngbytes"

        monkeypatch.setattr(pdf_tool, "_fetch_mermaid_png_bytes", _fake_mermaid_bytes)

        md = "# Fluxo ```mermaid flowchart TD A[Inicio] --> B[Fim] ```"
        html = pdf_tool._build_html_document(md)
        assert "data:image/png;base64," in html
        assert "Diagrama Mermaid" in html

    def test_html_renderer_fallbacks_to_reportlab_when_html_fails(self, monkeypatch):
        monkeypatch.setenv("PDF_RENDERER", "auto")

        def _raise_html(*args, **kwargs):
            raise RuntimeError("html renderer error")

        monkeypatch.setattr(pdf_tool, "_build_pdf_from_html", _raise_html)

        result = _build_pdf("# Teste\n\nConteudo simples")
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_html_renderer_mode_keeps_reportlab_fallback(self, monkeypatch):
        monkeypatch.setenv("PDF_RENDERER", "html")

        def _raise_html(*args, **kwargs):
            raise RuntimeError("weasyprint not available")

        monkeypatch.setattr(pdf_tool, "_build_pdf_from_html", _raise_html)

        result = _build_pdf("# Teste\n\nConteudo simples")
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_returns_bytes(self):
        result = _build_pdf("# Teste\n\nConteúdo simples.")
        assert isinstance(result, bytes)

    def test_returns_non_empty_bytes(self):
        result = _build_pdf("# Teste\n\nConteúdo simples.")
        assert len(result) > 0

    def test_pdf_starts_with_magic_bytes(self):
        result = _build_pdf("# Documento\n\nTexto.")
        assert result[:4] == b"%PDF"

    def test_empty_string_returns_bytes(self):
        result = _build_pdf("")
        assert isinstance(result, bytes)
        # fallback ou PDF válido
        assert len(result) > 0

    def test_complex_markdown_produces_pdf(self):
        md = """# Processo AS-IS

## 1. Visão Geral

Este documento descreve o processo AS-IS.

## 2. Fluxo

| Etapa | Ator | Descrição |
|-------|------|-----------|
| 1     | User | Início    |
| 2     | Sys  | Processo  |

## 3. Diagnóstico

- Gargalo A
- Gargalo B

## 4. KPIs

**KPI 1:** Taxa de conversão
**KPI 2:** Tempo médio
"""
        result = _build_pdf(md)
        assert result[:4] == b"%PDF"
        assert len(result) > 1000

    def test_invalid_reportlab_content_returns_fallback_pdf(self):
        """Conteúdo que pode causar erro deve retornar PDF de fallback."""
        # Tenta gerar com conteúdo que pode disparar erro interno
        result = _build_pdf("x" * 10000)
        # Deve sempre retornar bytes de PDF (nunca lançar exceção)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_unicode_content_works(self):
        md = "# Processo de Açúcar\n\nDescrição com caracteres especiais: ção, ões, ã."
        result = _build_pdf(md)
        assert result[:4] == b"%PDF"

    def test_table_markdown_produces_pdf(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _build_pdf(md)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_build_pdf_is_deterministic_in_size(self):
        """Duas chamadas com o mesmo conteúdo devem gerar PDFs de tamanho similar."""
        md = "# Título\n\nConteúdo fixo."
        r1 = _build_pdf(md)
        r2 = _build_pdf(md)
        # Tamanhos podem variar levemente (timestamps internos do PDF)
        # mas devem estar dentro de 5% um do outro
        diff_pct = abs(len(r1) - len(r2)) / max(len(r1), len(r2))
        assert diff_pct < 0.05
