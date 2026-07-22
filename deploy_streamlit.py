"""
Deploy do Streamlit Visualizador de Processos no Google Cloud Run.

Uso:
    python deploy_streamlit.py

O que este script faz:
    1. Cria a service account `streamlit-processos` (se não existir) e concede
       roles/cloudsql.client no projeto (Cloud SQL Python Connector).
    2. Faz o build da imagem Docker usando Google Cloud Build
       (contexto = raiz do projeto, Dockerfile = dataViz/Dockerfile).
    3. Faz push para Container Registry (gcr.io).
    4. Deploy no Cloud Run com a service account dedicada.
    5. Exibe a URL pública do serviço.

Pré-requisitos:
    - gcloud auth login / gcloud auth application-default login
    - gcloud config set project steady-computer-487217-p6
    - Cloud Build, Cloud Run, Container Registry e Cloud SQL Admin APIs habilitadas
    - Permissão de owner ou roles necessárias para criar SA e fazer deploy
    - POSTGRES_PASSWORD definida no .env (repassada ao Cloud Run)
"""

import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

# Força UTF-8 para evitar UnicodeEncodeError no Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Configurações ─────────────────────────────────────────────────────────────
PROJECT_ID        = "steady-computer-487217-p6"
REGION            = "us-central1"
SERVICE_NAME      = "streamlit-processos"
IMAGE             = f"gcr.io/{PROJECT_ID}/{SERVICE_NAME}:latest"
DOCKERFILE        = "dataViz/Dockerfile"
SA_NAME           = "streamlit-processos"
SA_EMAIL          = f"{SA_NAME}@{PROJECT_ID}.iam.gserviceaccount.com"
POSTGRES_USER     = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
if not POSTGRES_PASSWORD:
    print("⚠️  POSTGRES_PASSWORD não encontrada no .env — o app não conseguirá ler do Postgres em produção.")

# ── Helpers ───────────────────────────────────────────────────────────────────

# No Windows gcloud é um .cmd — prefixamos com ["cmd", "/c"] para o shell resolver
# o executável enquanto mantemos a lista de argumentos (sem problemas com espaços).
def _wrap(cmd: list[str]) -> list[str]:
    return (["cmd", "/c"] + cmd) if sys.platform == "win32" else cmd


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Executa um comando, exibindo-o antes de rodar."""
    print(f"   $ {' '.join(cmd)}")
    result = subprocess.run(_wrap(cmd), capture_output=False, text=True)
    if check and result.returncode != 0:
        print(f"\n❌  Comando falhou com código {result.returncode}.")
        sys.exit(result.returncode)
    return result


def run_capture(cmd: list[str]) -> str:
    """Executa um comando e retorna stdout sem exibir saída."""
    result = subprocess.run(_wrap(cmd), capture_output=True, text=True)
    return result.stdout.strip()


# ── 1. Service Account ────────────────────────────────────────────────────────
print()
print("=" * 70)
print("🔐  PASSO 1 — Service Account para o Cloud Run")
print("=" * 70)

existing_sa = run_capture([
    "gcloud", "iam", "service-accounts", "describe", SA_EMAIL,
    "--project", PROJECT_ID,
])
if existing_sa:
    print(f"   ✅  Service account já existe: {SA_EMAIL}")
else:
    print(f"   ➕  Criando service account: {SA_EMAIL}")
    run([
        "gcloud", "iam", "service-accounts", "create", SA_NAME,
        "--display-name", "Streamlit Visualizador de Processos",
        "--project", PROJECT_ID,
    ])

# Garante roles/cloudsql.client no projeto (Cloud SQL Python Connector)
print(f"   🔑  Concedendo roles/cloudsql.client em {PROJECT_ID}...")
run([
    "gcloud", "projects", "add-iam-policy-binding", PROJECT_ID,
    "--member", f"serviceAccount:{SA_EMAIL}",
    "--role", "roles/cloudsql.client",
])
print("   ✅  Permissão Cloud SQL concedida.")

# ── 2. Cloud Build ────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("🏗️   PASSO 2 — Build da imagem Docker via Cloud Build")
print("=" * 70)
print(f"   Contexto   : . (raiz do projeto)")
print(f"   Dockerfile : {DOCKERFILE}")
print(f"   Imagem     : {IMAGE}")
print()

run([
    "gcloud", "builds", "submit", ".",
    f"--config=cloudbuild_streamlit.yaml",
    "--project", PROJECT_ID,
])
print(f"\n   ✅  Imagem publicada: {IMAGE}")

# ── 3. Cloud Run Deploy ───────────────────────────────────────────────────────
print()
print("=" * 70)
print("🚀  PASSO 3 — Deploy no Cloud Run")
print("=" * 70)
print(f"   Serviço    : {SERVICE_NAME}")
print(f"   Região     : {REGION}")
print(f"   SA         : {SA_EMAIL}")
print()

run([
    "gcloud", "run", "deploy", SERVICE_NAME,
    "--image", IMAGE,
    "--region", REGION,
    "--platform", "managed",
    "--service-account", SA_EMAIL,
    "--allow-unauthenticated",   # remova esta linha para acesso autenticado (IAP/IAM)
    "--port", "8080",
    "--cpu", "1",
    "--memory", "512Mi",
    "--min-instances", "0",       # escala a zero quando ocioso (reduz custo)
    "--max-instances", "3",
    "--timeout", "300",           # 5 min (queries Postgres + render PDF/Mermaid)
    "--concurrency", "10",
    "--set-env-vars", f"POSTGRES_USER={POSTGRES_USER},POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
    "--project", PROJECT_ID,
])

# ── 4. Exibe URL do serviço ───────────────────────────────────────────────────
url = run_capture([
    "gcloud", "run", "services", "describe", SERVICE_NAME,
    "--region", REGION,
    "--project", PROJECT_ID,
    "--format", "value(status.url)",
])

print()
print("=" * 70)
print("✅  Deploy concluído com sucesso!")
print("=" * 70)
print(f"   Serviço : {SERVICE_NAME}")
print(f"   Região  : {REGION}")
print(f"   URL     : {url}")
print()
print("Para acessar o app:")
print(f"   {url}")
print()
print("Para ver os logs:")
print(f"   gcloud run services logs read {SERVICE_NAME} --region {REGION} --project {PROJECT_ID} --limit 50")
print()
print("Para redeploy após alterações em streamlit_grafo.py:")
print(f"   python deploy_streamlit.py")
print("=" * 70)
