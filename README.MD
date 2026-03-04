# agente-processos

Sistema multiagente para extração, estruturação e visualização de processos de negócio **AS-IS**, construído sobre o **Google ADK (Agent Development Kit)** e modelos **Gemini**.

---

## Visão Geral

O sistema transforma documentos de processos de negócio (PDF, DOCX, imagens) em:

- **Planilha Excel** estruturada (hierarquia N0–N4)
- **Documento PDF** completo no formato AS-IS
- **Scripts Mermaid** persistidos no Spanner (1 por N2_Processo)
- **Interface de visualização** interativa (Streamlit)

---

## Arquitetura

```
document_processor  (agente raiz / orquestrador)
├── preparar_state_inicial          (tool local)
├── as_is                           (sub-agente: extração JSON)
├── save_to_firestore_from_state    (tool: JSON → Firestore)
├── generate_xlsx_from_state        (tool: JSON → Excel)
├── agente_gerador_pdf_md           (sub-agente: JSON → Markdown)
├── generate_pdf_from_state         (tool: Markdown → PDF + Mermaid → Spanner)
└── save_mermaid_from_state         (tool: Mermaid → Spanner N2_Mermaid)
```

### Modelos LLM

| Agente | Modelo | ThinkingConfig |
|--------|--------|----------------|
| `document_processor` | `gemini-2.5-flash` | `thinking_budget=0` |
| `as_is` | `gemini-2.5-flash` | Padrão ADK |
| `agente_gerador_pdf_md` | `gemini-2.5-flash` | `thinking_budget=8192` |

---

## Estrutura do Projeto

```
agente-processos-main/
│
├── document_processor/          # Agente raiz / orquestrador
│   ├── agent.py
│   └── tools/
│       ├── generate_artifacts.py   # Tools determinísticas (XLSX, PDF, Mermaid)
│       └── spanner_tool.py         # Persistência no Spanner
│
├── as_is/                       # Sub-agente de extração estruturada
│   └── agent.py
│
├── agente_gerador_pdf_md/       # Sub-agente gerador de Markdown/PDF
│   ├── agent.py
│   └── tools/
│       └── markdown_to_pdf_tool.py
│
├── agent_tools/                 # Utilitários compartilhados
│   └── json_to_xlsx.py
│
├── dataViz/                     # Interface de visualização (Streamlit)
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

## Database — Google Cloud Spanner (`db-agente-processo`)

### Tabelas

| Tabela | Descrição | PK |
|--------|-----------|-----|
| `N0_Frente` | Frente de negócio | `id` |
| `N1_MacroProcesso` | Macro processo | `id` |
| `N2_Processo` | Processo | `id` |
| `N3_Tarefa` | Tarefa | `id` |
| `N4_Etapa` | Etapa | `id` |
| `N5_Atributos` | Atributos da etapa | `id` |
| `N2_Mermaid` | Script Mermaid por processo | `n2_id` (FK → N2_Processo) |

### Tabelas de Relacionamento (Arestas)

| Tabela | Relacionamento |
|--------|---------------|
| `Edge_Has_N1` | N0_Frente → N1_MacroProcesso |
| `Edge_Has_N2` | N1_MacroProcesso → N2_Processo |
| `Edge_Has_N3` | N2_Processo → N3_Tarefa |
| `Edge_Has_N4` | N3_Tarefa → N4_Etapa |
| `Edge_Has_N5` | N4_Etapa → N5_Atributos |
| `Edge_Has_Mermaid` | N2_Processo → N2_Mermaid |

### Cardinalidade Mermaid

> **1 script Mermaid : 1 N2_Processo** — a PK `n2_id` garante unicidade. O mesmo script gerado para o documento é persistido para cada N2 distinto presente no JSON.

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
- Pesquisa no Spanner (`N0_Frente`) com filtro `LIKE` (busca parcial por nome da frente)
- Exibe cards com: nome da frente, macro processos vinculados e total de tarefas
- Conexão reutilizada via `@st.cache_resource`

#### Área Principal — Detalhe do Processo

**Aba: Passo a Passo**
- Hierarquia completa carregada do Spanner: N1 → N2 → N3 → N4 → N5
- Cada Macro Processo em `st.expander`
- Por etapa: descrição, entradas/saídas, sistemas envolvidos, KPIs, oportunidades de melhoria
- Métricas do processo: total de etapas, tarefas, processos e macro processos

**Aba: Diagrama de Fluxo**
- Lê o script Mermaid da tabela `N2_Mermaid` para cada N2 do processo selecionado
- Renderiza o diagrama via `mermaid.ink/img/{base64}` → PNG → `st.image()`
- Fallback automático para CDN Mermaid JS (`components.html`) se `mermaid.ink` não responder
- Normalização automática do script Mermaid (colapsa quebras de linha dentro de labels, CRLF → LF)

---

## Pipeline de Processamento

```
1. Usuário envia documento (PDF/DOCX/imagem)
       ↓
2. preparar_state_inicial()
   └→ state["pdf_model_md"] = template Markdown
       ↓
3. as_is_agent(documento)
   └→ state["pdf_input_json"] = JSON N0–N4
       ↓
4. save_to_firestore_from_state()
   └→ Firestore: coleção 'processos'
       ↓
5. generate_xlsx_from_state()
   └→ Artefato: processos.xlsx
       ↓
6. pdf_subagent()
   └→ state["pdf_markdown"] = Markdown completo AS-IS
       ↓
7. generate_pdf_from_state()
   ├→ Artefato: documento_processo.pdf
   └→ Side-effect: _upsert_mermaid_sync() → Spanner N2_Mermaid
       ↓
8. save_mermaid_from_state()
   └→ Spanner: N2_Mermaid (fallback / idempotente)
```

### Artefatos Gerados

| Arquivo | Tipo | Download ADK |
|---------|------|:---:|
| `processos.xlsx` | Excel estruturado N0–N4 | ✅ |
| `documento_processo.pdf` | Documento AS-IS completo | ✅ |

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
```

### Credenciais GCP (`credentials.json`)

Service account com permissões:
- `roles/datastore.user` — Firestore
- `roles/spanner.databaseUser` — Spanner

> Não versionar. Adicionar ao `.gitignore`.

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

| Dado | Destino | Quando |
|------|---------|--------|
| JSON N0–N4 | Firestore (`processos`) | Após extração pelo `as_is` |
| Hierarquia completa | Spanner (`N0`–`N5` + arestas) | Durante persistência do grafo |
| Script Mermaid | Spanner (`N2_Mermaid`) | Side-effect de `generate_pdf_from_state` |
| Excel | ADK artifact | Tool `generate_xlsx_from_state` |
| PDF | ADK artifact | Tool `generate_pdf_from_state` |

---

## Deploy no Vertex AI Agent Engine

O script `deploy.py` publica o agente no **Vertex AI Agent Engine** (Reasoning Engine).

### Pré-requisitos

- Google Cloud SDK (`gcloud`) instalado e autenticado
- APIs habilitadas: `aiplatform`, `firestore`, `spanner`, `cloudbuild`, `storage`
- Bucket GCS de staging: `gs://agente-processos-staging`

### Executar o deploy

```bash
python deploy.py
```

> **Bug na imagem base `reasoning-engine-py311:prod`:** A imagem atual não cria `/code/.venv/bin/python` e executa como não-root, causando falha no build. O script `installation_scripts/fix_venv.sh` aplica o workaround automaticamente via `build_options.installation_scripts`.

---

## Documentação Adicional

- **`AGENTS.MD`** — arquitetura detalhada, responsabilidades, state compartilhado, guardrails e convenções de desenvolvimento de cada agente.

---

## Observações

- O `document_processor` usa `thinking_budget=0` para evitar respostas vazias ao processar PDFs com labels de confidencialidade (MSIP).
- A persistência do Mermaid é **side-effect determinístico** de `generate_pdf_from_state` — não depende do LLM chamar `save_mermaid_from_state`.
- Todas as operações de I/O (Firestore, Spanner, Excel, PDF) são executadas em `asyncio.to_thread()` para não bloquear o event loop do ADK.
- O UUID de cada nó é gerado via **UUID v5 determinístico** (`_make_id(n0, n1, n2, ...)`) — reprocessar o mesmo documento é idempotente.
