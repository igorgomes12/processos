"""
Deploy do agente-processos no Vertex AI Agent Engine.

Uso:
    python deploy.py

Pré-requisitos:
    - gcloud auth application-default login
    - gcloud config set project steady-computer-487217-p6
    - GOOGLE_API_KEY definido no ambiente (ou no .env)

PERMISSÕES IAM NECESSÁRIAS PARA O FIRESTORE (banco "as-is-processes"):
    O Vertex AI Reasoning Engine usa a service account:
        service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com

    Essa service account precisa de roles/datastore.user para gravar no Firestore.
    O script exibe o comando gcloud correto após o deploy.
    Ou execute manualmente:
        PROJECT_NUMBER=$(gcloud projects describe steady-computer-487217-p6 --format="value(projectNumber)")
        gcloud projects add-iam-policy-binding steady-computer-487217-p6 \\
            --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \\
            --role="roles/datastore.user"
"""

import os
import sys

# Força UTF-8 no stdout/stderr para evitar UnicodeEncodeError com emojis no Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import google.auth
import google.auth.transport.requests
from dotenv import load_dotenv

load_dotenv()

import vertexai
from vertexai import agent_engines

# ── Configurações do projeto ──────────────────────────────────────────────────
PROJECT_ID     = "steady-computer-487217-p6"
LOCATION       = "us-central1"
STAGING_BUCKET = "gs://agente-processos-staging"

# ── Variáveis de ambiente obrigatórias ────────────────────────────────────────
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("⚠️  GOOGLE_API_KEY não encontrada no .env — só necessária para testes locais.")
    print("   O Reasoning Engine usará ADC (Vertex AI mode) sem API key.")

MODEL = os.getenv("MODEL", "gemini-2.5-flash")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
if not POSTGRES_PASSWORD:
    print("⚠️  POSTGRES_PASSWORD não encontrada no .env — save_to_postgres_from_state falhará em produção.")

# ── Carrega credenciais ADC explicitamente e define quota project ─────────────
creds, _ = google.auth.default()
req = google.auth.transport.requests.Request()
creds.refresh(req)
# Garante que o quota project aponta para o projeto correto (necessário para gRPC)
if hasattr(creds, "with_quota_project"):
    creds = creds.with_quota_project(PROJECT_ID)

print(f"🚀 Iniciando deploy no Vertex AI Agent Engine...")
print(f"   Projeto  : {PROJECT_ID}")
print(f"   Região   : {LOCATION}")
print(f"   Bucket   : {STAGING_BUCKET}")
print(f"   Modelo   : {MODEL}")

vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET, credentials=creds)

# ── Importa o agente raiz APÓS o vertexai.init() ─────────────────────────────
from document_processor.agent import root_agent, as_is_root_agent

# ── Remove callbacks de logging local (não serializáveis pelo cloudpickle) ───
# No Vertex AI Agent Engine o logging é feito via Cloud Logging, não via
# callbacks locais que contêm threading.Lock() não-picklable.
def _strip_callbacks(agent):
    """Remove recursivamente before/after_tool_callback de um agente e seus sub-agentes."""
    agent.before_tool_callback = None
    agent.after_tool_callback = None
    for tool in getattr(agent, "tools", []) or []:
        sub = getattr(tool, "agent", None)
        if sub is not None:
            _strip_callbacks(sub)

_strip_callbacks(root_agent)
# Desde a refatoração para o pipeline de 2 fases (run_deterministic_pipeline_
# phase1/phase2), a geração de conteúdo não usa mais AgentTool/sub-agentes
# ADK — chama o modelo direto via document_processor/tools/llm_calls.py,
# reaproveitando só o TEXTO de instrução dos agentes auxiliares (constantes
# ENHANCED_INSTRUCTION, strings simples, triviais de serializar). O único
# objeto Agent auxiliar ainda referenciado por document_processor.agent é
# as_is_root_agent (só para ler `.instruction`), então só ele precisa de
# strip aqui — agente_gerador_pdf_md/agente_gerador_md_tobe não são mais
# importados como objetos Agent neste módulo.
_strip_callbacks(as_is_root_agent)
print("   Callbacks    : removidos (não serializáveis para deploy)")

# ── Deploy ────────────────────────────────────────────────────────────────────
remote_agent = agent_engines.create(
    agent_engine=root_agent,
    requirements=[
        # Versões alinhadas ao ambiente local (venv Python 3.14) para garantir
        # que o build remoto use exatamente os mesmos pacotes e evitar conflitos.
        "google-cloud-aiplatform[adk,agent_engines]==1.162.0",
        "google-adk==2.5.0",
        "google-genai==2.13.0",
        "cloudpickle==3.1.2",       # necessário para serialização do agente
        "pydantic==2.13.4",         # necessário pelo google-adk/vertexai
        "cloud-sql-python-connector[pg8000]",  # persistência no Postgres (save_to_postgres_from_state)
        "google-cloud-logging",
        "google-cloud-storage>=3.0.0",  # evita FutureWarning do google-cloud-aiplatform
        "pandas",
        "openpyxl",
        "reportlab",
        "python-dotenv",
        "python-docx",              # necessário para leitura de arquivos DOCX
    ],
    extra_packages=[
        "./agent_tools",
        "./as_is",
        "./agente_gerador_pdf_md",
        "./agente_gerador_md_tobe",
        "./document_processor",
        "./logger",
        "./prompts",
    ],
    # NOTA: build_options.installation_scripts com fix_venv.sh foi removido nesta
    # tentativa. O workaround era para um bug na imagem base reasoning-engine-py311:prod
    # (2026-03-02); o build atual usa assembly-service-py314:prod (imagem diferente,
    # Python 3.14) e o build falha em "poetry: Too many levels of symbolic links" logo
    # no passo de instalação de requirements — antes mesmo de chegar no passo de
    # compileall que o workaround corrigia. Testando se o workaround (obsoleto para
    # esta imagem) é a própria causa do encadeamento de shebang que gera o ELOOP.
    display_name="agente-processos",
    description="Pipeline BPM AS-IS: extração JSON N0-N4 + geração de planilha XLSX e documento PDF",
    env_vars={
        # Usa Vertex AI mode com ADC (Application Default Credentials).
        # GOOGLE_CLOUD_PROJECT e GOOGLE_CLOUD_LOCATION são injetados
        # automaticamente pelo Reasoning Engine (são vars reservadas).
        # Com GOOGLE_GENAI_USE_VERTEXAI=1, o google-genai usa ADC + project/location
        # sem conflito com GOOGLE_API_KEY, resolvendo o AttributeError _http_options.
        "GOOGLE_GENAI_USE_VERTEXAI": "1",
        "MODEL": MODEL,
        "POSTGRES_USER": POSTGRES_USER,
        "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
        # Desativa explicitamente o exportador de Cloud Trace do template
        # AdkApp (vertexai/agent_engines/templates/adk.py). Esse exportador
        # usa uma AuthorizedSession síncrona (google.auth.transport.requests)
        # e faz force_flush() automático após CADA turno — suspeito de
        # investigação nos travamentos intermitentes de 2026-08-06 (esse
        # caminho só existe no wrapper de deploy do Agent Engine, nunca no
        # InMemoryRunner local, o que bateria com o problema ser 100%
        # exclusivo de produção). Sem esta variável e sem enable_tracing=True
        # explícito, o tracing já deveria ficar desligado por padrão (ver
        # truth table em adk.py:_tracing_enabled) — definimos aqui mesmo
        # assim para eliminar qualquer ambiguidade.
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "false",
        # Desativa a renderização online do diagrama Mermaid (chamada de rede
        # externa para https://mermaid.ink dentro de _build_pdf). Confirmado
        # em produção (2026-08-06) travando indefinidamente para documentos
        # maiores/diagramas mais complexos — nem o timeout=15 do urlopen nem
        # o timeout externo de 120s do pipeline conseguiram interromper.
        # PDF cai para o código Mermaid em texto (comportamento já existente
        # em caso de falha), sem tentar a chamada de rede. Continua ativado
        # por padrão localmente para teste/desenvolvimento.
        "MERMAID_RENDER_ENABLED": "false",
    },
)

print()
print("✅ Deploy concluído com sucesso!")
print(f"   Resource name : {remote_agent.resource_name}")
print(f"   Display name  : agente-processos")
print()
print("Para usar o agente remotamente:")
print(f'   agent_engines.get("{remote_agent.resource_name}")')

# ── Verificação de permissão IAM do Cloud SQL ─────────────────────────────────
print()
print("=" * 70)
print("⚠️  ATENÇÃO: PERMISSÃO CLOUD SQL NECESSÁRIA")
print("=" * 70)
print()
print("A mesma service account do Reasoning Engine precisa de acesso ao Cloud SQL")
print("(Cloud SQL Python Connector usa roles/cloudsql.client, escopo de projeto).")
print()
try:
    import subprocess
    result = subprocess.run(
        ["gcloud", "projects", "describe", PROJECT_ID, "--format=value(projectNumber)"],
        capture_output=True, text=True, timeout=15,
    )
    project_number = result.stdout.strip()
    sa_cloudsql = f"service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
    print(f"   {sa_cloudsql}")
    print()
    print("Execute o seguinte comando para conceder acesso ao Cloud SQL:")
    print()
    print(f'   gcloud projects add-iam-policy-binding {PROJECT_ID} \\')
    print(f'       --member="serviceAccount:{sa_cloudsql}" \\')
    print(f'       --role="roles/cloudsql.client"')
except Exception:
    print("   service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com")
    print()
    print("Execute para obter o PROJECT_NUMBER e conceder acesso ao Cloud SQL:")
    print()
    print(f"   PROJECT_NUMBER=$(gcloud projects describe {PROJECT_ID} --format='value(projectNumber)')")
    print(f'   gcloud projects add-iam-policy-binding {PROJECT_ID} \\')
    print(f'       --member="serviceAccount:service-${{PROJECT_NUMBER}}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \\')
    print(f'       --role="roles/cloudsql.client"')
print()
print("Sem essa permissão o registro no Postgres falhará com PERMISSION_DENIED.")
print("(o erro será visível nos logs do Cloud Logging / stderr do agente).")
print("=" * 70)
