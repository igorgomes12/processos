# agente-processos

Sistema multiagente para extração, estruturação e visualização de processos de negócio **AS-IS**, com geração complementar de proposta **TO-BE**, construído sobre o **Google ADK (Agent Development Kit)** e modelos **Gemini**.

---

## Visão Geral

O sistema transforma documentos de processos de negócio (PDF, DOCX, imagens) em:

- **Planilha Excel** estruturada (hierarquia N0–N4)
- **Documento PDF AS-IS** completo, com diagrama de fluxo Mermaid
- **Documento PDF TO-BE** — proposta de processo futuro gerada a partir do AS-IS
- **Persistência estruturada** do grafo AS-IS (N0–N5 + arestas) e do documento **TO-BE** (Markdown, por N2) no Postgres, e cópia do JSON bruto AS-IS no Firestore
- **Interface de visualização** interativa (Streamlit), lendo do Postgres — inclui aba dedicada à proposta TO-BE

---

## Arquitetura

```
document_processor  (agente raiz / orquestrador)
├── preparar_state_inicial          (tool local: carrega template Markdown)
├── as_is_agent                     (sub-agente: documento → JSON N0–N4)
├── save_to_firestore_from_state    (tool: JSON → Firestore, cópia bruta)
├── save_to_postgres_from_state     (tool: JSON → Postgres, grafo N0–N5 + arestas)
├── pdf_subagent (agente_gerador_pdf_md)   (sub-agente: JSON → Markdown AS-IS)
├── generate_xlsx_from_state        (tool: JSON → Excel)
├── generate_pdf_from_state         (tool: Markdown AS-IS → PDF + Mermaid → Postgres)
├── save_mermaid_from_state         (tool: Mermaid → Postgres n2_mermaid, best-effort)
├── tobe_subagent_tool (agente_gerador_md_tobe)  (sub-agente: JSON AS-IS → Markdown TO-BE)
└── generate_pdf_tobe_from_state    (tool: Markdown TO-BE → PDF + Postgres n2_tobe_documento)
```

### Modelos LLM

| Agente | Modelo | ThinkingConfig |
|--------|--------|----------------|
| `document_processor` | `gemini-2.5-flash` (via env `MODEL`) | `thinking_budget=0` |
| `as_is` | `gemini-2.5-flash` (via env `MODEL`) | Padrão ADK |
| `agente_gerador_pdf_md` | `gemini-2.5-flash` (hardcoded) | `thinking_budget=8192` |
| `agente_gerador_md_tobe` | `gemini-2.5-flash` (hardcoded) | `thinking_budget=8192` |

---

## Estrutura do Projeto

```
agente-processos-main/
│
├── document_processor/          # Agente raiz / orquestrador
│   ├── agent.py
│   └── tools/
│       ├── generate_artifacts.py   # Tools determinísticas (XLSX, PDF, Mermaid)
│       └── postgres_tool.py        # Persistência no Postgres
│
├── as_is/                       # Sub-agente de extração estruturada
│   └── agent.py
│
├── agente_gerador_pdf_md/       # Sub-agente gerador de Markdown/PDF AS-IS
│   ├── agent.py
│   └── tools/
│       └── markdown_to_pdf_tool.py
│
├── agente_gerador_md_tobe/      # Sub-agente gerador de Markdown TO-BE
│   ├── agent.py
│   └── prompt-gerador-markdown-tobe.txt
│
├── agent_tools/                 # Utilitários compartilhados
│   └── json_to_xlsx.py
│
├── dataViz/                     # Interface de visualização (Streamlit) — lê do Postgres (AS-IS + TO-BE)
│   └── streamlit_grafo.py
│
├── prompts/                     # Prompts externos dos sub-agentes
│   └── prompt_agente_gerador_pdf.txt
│
├── AGENTS.MD                    # Documentação detalhada dos agentes
├── README.MD
├── requirements.txt
├── .env                         # Variáveis de ambiente (não versionado)
└── credentials.json             # Service account GCP (não versionado)
```

---

## Database — Cloud SQL for PostgreSQL (`db-agente-processo`)

Instância: `agente-processos-db` (`steady-computer-487217-p6:us-east1:agente-processos-db`).
Schema completo em `installation_scripts/postgres_schema.sql`.

### Tabelas

| Tabela | Descrição | PK |
|--------|-----------|-----|
| `n0_frente` | Frente de negócio | `id` |
| `n1_macroprocesso` | Macro processo | `id` |
| `n2_processo` | Processo | `id` |
| `n3_tarefa` | Tarefa | `id` |
| `n4_etapa` | Etapa | `id` |
| `n5_atributos` | Atributos da etapa | `id` |
| `n2_mermaid` | Script Mermaid por processo | `n2_id` (FK → n2_processo) |
| `n2_tobe_documento` | Documento Markdown TO-BE por processo | `n2_id` (FK → n2_processo) |

### Tabelas de Relacionamento (Arestas)

| Tabela | Relacionamento |
|--------|---------------|
| `edge_has_n1` | n0_frente → n1_macroprocesso |
| `edge_has_n2` | n1_macroprocesso → n2_processo |
| `edge_has_n3` | n2_processo → n3_tarefa |
| `edge_has_n4` | n3_tarefa → n4_etapa |
| `edge_has_n5` | n4_etapa → n5_atributos |
| `edge_has_mermaid` | n2_processo → n2_mermaid |
| `edge_has_tobe` | n2_processo → n2_tobe_documento |

### Cardinalidade Mermaid

> **1 script Mermaid : 1 N2_Processo** — a PK `n2_id` garante unicidade. O mesmo script gerado para o documento é persistido para cada N2 distinto presente no JSON.

### Cardinalidade TO-BE

> **1 documento TO-BE : 1 N2_Processo** — mesmo padrão do Mermaid. A PK `n2_id` de `n2_tobe_documento` garante unicidade; o mesmo Markdown TO-BE gerado para o documento é persistido para cada N2 distinto presente no JSON AS-IS.

---

## Interface de Visualização — Streamlit (`dataViz/`)

### Como executar

```bash
cd agente-processos-main
streamlit run dataViz/streamlit_grafo.py
```

### Funcionalidades

#### Sidebar — Pesquisa de Processos
- Campo de busca com **Enter** ou botão **Buscar**
- Pesquisa no Postgres (`n0_frente`) com filtro `ILIKE` (busca parcial por nome da frente)
- Exibe cards com: nome da frente, macro processos vinculados e total de tarefas
- Conexão reutilizada via `@st.cache_resource`

#### Área Principal — Detalhe do Processo

**Aba: Passo a Passo**
- Hierarquia completa carregada do Postgres: N1 → N2 → N3 → N4 → N5
- Cada Macro Processo em `st.expander`
- Por etapa: descrição, entradas/saídas, sistemas envolvidos, KPIs, oportunidades de melhoria
- Métricas do processo: total de etapas, tarefas, processos e macro processos

**Aba: Diagrama de Fluxo**
- Lê o script Mermaid da tabela `N2_Mermaid` para cada N2 do processo selecionado
- Renderiza o diagrama via `mermaid.ink/img/{base64}` → PNG → `st.image()`
- Fallback automático para CDN Mermaid JS (`components.html`) se `mermaid.ink` não responder
- Normalização automática do script Mermaid (colapsa quebras de linha dentro de labels, CRLF → LF)

**Aba: Proposta TO-BE**
- Lê o Markdown TO-BE da tabela `n2_tobe_documento` para cada N2 do processo selecionado
- Renderiza via `st.markdown()` sem `unsafe_allow_html` (conteúdo gerado por LLM, tratado como texto/markdown puro — nunca como HTML)
- Exibe aviso caso o processo ainda não tenha proposta TO-BE gerada

---

## Pipeline de Processamento

O `document_processor` executa as 11 etapas abaixo em sequência, num único turno,
sem interromper mesmo se uma tool falhar (política de continuidade — só responde
ao usuário depois da etapa 11):

```
1. Usuário envia documento (PDF/DOCX/imagem)
       ↓
2. preparar_state_inicial()
   └→ state["pdf_model_md"] = template Markdown
       ↓
3. as_is_agent(documento)                         ← LLM
   └→ state["pdf_input_json"] = JSON N0–N4
       ↓
4. save_to_firestore_from_state()                 ← tool determinística
   └→ Firestore: coleção 'processos' (cópia bruta do JSON)
       ↓
5. save_to_postgres_from_state()                  ← tool determinística
   └→ Postgres: grafo n0_frente…n5_atributos + arestas (upsert idempotente)
       ↓
6. pdf_subagent()                                 ← LLM
   └→ state["pdf_markdown"] = Markdown completo AS-IS
       ↓
7. generate_xlsx_from_state()
   └→ Artefato: processos.xlsx
       ↓
8. generate_pdf_from_state()
   ├→ Artefato: documento_processo.pdf
   └→ Side-effect: _upsert_mermaid_sync() → Postgres n2_mermaid
       ↓
9. save_mermaid_from_state()                      ← fallback best-effort, idempotente
   └→ Postgres: n2_mermaid (garante persistência mesmo se o side-effect da etapa 8 falhar)
       ↓
10. tobe_subagent_tool()                          ← LLM (lê pdf_input_json)
    └→ state["tobe_markdown"] = Markdown da proposta TO-BE
       ↓
11. generate_pdf_tobe_from_state()
    ├→ Artefato: documento_processo_tobe.pdf
    └→ Side-effect: _upsert_tobe_sync() → Postgres n2_tobe_documento
```

### Artefatos Gerados

| Arquivo | Tipo | Persistido em banco? | Download ADK |
|---------|------|:---:|:---:|
| `processos.xlsx` | Excel estruturado N0–N4 | Dados-fonte sim (Postgres/Firestore) | ✅ |
| `documento_processo.pdf` | Documento AS-IS completo | Dados-fonte sim (Postgres/Firestore) | ✅ |
| `documento_processo_tobe.pdf` | Documento TO-BE (proposta futura) | ✅ Sim (Markdown em `n2_tobe_documento`, por N2) | ✅ |

---

## Schema JSON (N0–N4)

```json
{
  "schemaVersion": "1.0.0",
  "meta": { "model": "N0-N4+atributos", "locale": "pt-BR" },
  "columns": [
    { "key": "N0", "label": "Frente (N0)" },
    { "key": "N1", "label": "Macro processo (N1)" },
    { "key": "N2", "label": "Processo (N2)" },
    { "key": "N3", "label": "Tarefa (N3)" },
    { "key": "N4", "label": "Etapa (N4)" },
    { "key": "descricao" },
    { "key": "entradas" },
    { "key": "saidas" },
    { "key": "sistemasEnvolvidos" },
    { "key": "kpis" },
    { "key": "oportunidadesMelhoria" }
  ],
  "rows": [ { "rowId": "...", "N0": "...", "N1": "...", "N2": "...",
              "N3": "...", "N4": "...", "descricao": "...",
              "entradas": [], "saidas": [], "sistemasEnvolvidos": [],
              "kpis": [], "oportunidadesMelhoria": [] } ]
}
```

---

## Configuração

### Variáveis de Ambiente (`.env`)

```env
GOOGLE_API_KEY=<sua_chave_google_ai_studio>
MODEL=gemini-2.5-flash          # opcional
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<senha_da_instancia_cloud_sql>
```

### Credenciais GCP (`credentials.json`)

Usado **apenas em desenvolvimento local**. Prioridade de autenticação em todo o
projeto (Firestore e Postgres): 1) `credentials.json` local, se existir; 2)
Application Default Credentials (ADC) caso contrário — é o que roda no Vertex
AI Agent Engine e no Cloud Run.

Service account local (`agente-processos-app@...`) com permissões:
- `roles/datastore.user` — Firestore
- `roles/cloudsql.client` — Cloud SQL (Postgres, via Cloud SQL Python Connector)
- `roles/logging.logWriter` — Cloud Logging

Em produção (Vertex AI Agent Engine), quem grava no Firestore/Postgres é a
service account **gerenciada automaticamente pelo Reasoning Engine**
(`service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`), não
a `credentials.json` local. Ela precisa das mesmas 2 roles (`datastore.user` e
`cloudsql.client`) concedidas manualmente uma vez no projeto — `deploy.py`
imprime o comando `gcloud` exato após o deploy.

> `credentials.json` não é versionado. Já está no `.gitignore`.

### Instalação

```bash
pip install -r requirements.txt
```

### Executar o Agente (ADK)

```bash
adk web
```

### Executar a Interface de Visualização

```bash
streamlit run dataViz/streamlit_grafo.py
```

---

## Persistência de Dados

| Dado | Destino | Quando | Consumido por |
|------|---------|--------|----------------|
| JSON N0–N4 (bruto) | Firestore (database `as-is-processes`, coleção `processos`) | `save_to_firestore_from_state` | Ninguém hoje (write-only — nenhum código lê de volta) |
| Grafo AS-IS completo (N0–N5 + arestas) | Postgres (`n0_frente`…`n5_atributos` + `edge_has_n*`) | `save_to_postgres_from_state` | **DataViz** (única fonte de leitura da interface) |
| Script Mermaid | Postgres (`n2_mermaid` + `edge_has_mermaid`) | `generate_pdf_from_state` (side-effect) e `save_mermaid_from_state` (fallback) | DataViz (aba Diagrama de Fluxo) |
| Documento TO-BE (Markdown) | Postgres (`n2_tobe_documento` + `edge_has_tobe`) | `generate_pdf_tobe_from_state` (side-effect) | **DataViz** (aba Proposta TO-BE) |
| Excel | ADK artifact (download da sessão) | `generate_xlsx_from_state` | Download manual |
| PDF AS-IS | ADK artifact (download da sessão) | `generate_pdf_from_state` | Download manual |
| PDF TO-BE | ADK artifact (download da sessão) | `generate_pdf_tobe_from_state` | Download manual (o conteúdo também fica no Postgres como Markdown, diferente do PDF em si) |

---

## Deploy no Vertex AI Agent Engine

O script `deploy.py` publica o agente no **Vertex AI Agent Engine** (Reasoning Engine).

### Pré-requisitos

- Google Cloud SDK (`gcloud`) instalado e autenticado
- APIs habilitadas: `aiplatform`, `firestore`, `sqladmin`, `cloudbuild`, `storage`
- Bucket GCS de staging: `gs://agente-processos-staging`

### Executar o deploy

```bash
python deploy.py
```

> **Histórico:** a imagem base usada pelo Reasoning Engine mudou de
> `reasoning-engine-py311:prod` para `assembly-service-py314:prod` (Python 3.14).
> O workaround antigo (`installation_scripts/fix_venv.sh`, que criava um wrapper
> em `/code/.venv/bin/python` para contornar um bug de permissão no `compileall`
> da imagem py311) ficou obsoleto e passou a **quebrar o build** na imagem nova —
> o wrapper (um script Python, não um binário/symlink real) gera uma cadeia de
> shebang que o `poetry` da imagem não resolve, falhando com
> `poetry: Too many levels of symbolic links`. O workaround foi removido de
> `deploy.py` (não é mais necessário na imagem atual). Se esse erro específico
> voltar a aparecer em uma imagem futura, o script `fix_venv.sh` ainda existe em
> `installation_scripts/` como referência, mas precisaria ser adaptado (detectar
> `python3.14`, não recriar `/code/.venv/bin/python` como script simples).

---

## Documentação Adicional

- **`AGENTS.MD`** — arquitetura detalhada, responsabilidades, state compartilhado, guardrails e convenções de desenvolvimento de cada agente.

---

## Limitações Conhecidas

- **Firestore é write-only.** O JSON bruto salvo em `save_to_firestore_from_state`
  não é lido de volta por nenhuma parte do sistema hoje (nem pelo agente, nem
  pelo DataViz). Funciona como um backup do JSON original, não como fonte de
  dados ativa.

---

## Observações

- O `document_processor` usa `thinking_budget=0` para evitar respostas vazias ao processar PDFs com labels de confidencialidade (MSIP).
- A persistência do Mermaid é **side-effect determinístico** de `generate_pdf_from_state` — não depende do LLM chamar `save_mermaid_from_state`.
- A persistência do TO-BE é **side-effect determinístico** de `generate_pdf_tobe_from_state` (mesmo padrão do Mermaid) — não depende de uma tool adicional chamada pelo LLM.
- Todas as operações de I/O (Firestore, Postgres, Excel, PDF) são executadas em `asyncio.to_thread()` para não bloquear o event loop do ADK.
- O UUID de cada nó é gerado via **UUID v5 determinístico** (`_make_id(n0, n1, n2, ...)`) — reprocessar o mesmo documento é idempotente.
