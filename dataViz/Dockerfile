# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Streamlit Visualizador de Processos
#
# Contexto de build: RAIZ do projeto (c:\Projects\agente-processos-main)
# Exemplo de build manual:
#   gcloud builds submit . \
#       --tag=gcr.io/steady-computer-487217-p6/streamlit-grafo:latest \
#       --dockerfile=dataViz/Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Evita prompts interativos durante a instalação de pacotes do sistema
ENV DEBIAN_FRONTEND=noninteractive
# Força stdout/stderr sem buffer (logs visíveis em tempo real no Cloud Logging)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ── Instala dependências Python ───────────────────────────────────────────────
COPY dataViz/requirements_streamlit.txt requirements_streamlit.txt
RUN pip install --no-cache-dir -r requirements_streamlit.txt

# ── Copia módulo agente_gerador_pdf_md (necessário para _clean_mermaid_code) ─
COPY agente_gerador_pdf_md/__init__.py             agente_gerador_pdf_md/__init__.py
COPY agente_gerador_pdf_md/tools/__init__.py       agente_gerador_pdf_md/tools/__init__.py
COPY agente_gerador_pdf_md/tools/markdown_to_pdf_tool.py \
     agente_gerador_pdf_md/tools/markdown_to_pdf_tool.py

# ── Copia o aplicativo Streamlit ─────────────────────────────────────────────
COPY dataViz/streamlit_grafo.py dataViz/streamlit_grafo.py

# ── Tema fixo (light) — evita que o app siga o dark-mode do navegador/SO,
#    o que deixava widgets nativos (expander) e o Mermaid ilegíveis ─────────
COPY .streamlit/config.toml .streamlit/config.toml

# Cloud Run define $PORT em tempo de execução (padrão = 8080).
# Streamlit usa a variável via shell expansion no CMD abaixo.
EXPOSE 8080

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD ["sh", "-c", \
     "streamlit run dataViz/streamlit_grafo.py \
      --server.port=${PORT:-8080} \
      --server.address=0.0.0.0 \
      --server.headless=true \
      --browser.gatherUsageStats=false"]
