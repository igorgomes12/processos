# AGENTS.MD — Documentação dos Agentes

> Este arquivo descreve a arquitetura, responsabilidades, ferramentas e fluxo de comunicação de cada agente do sistema **agente-processos**.

---

## Visão Geral da Arquitetura

O sistema é composto por **3 agentes** organizados em hierarquia orquestradora/sub-agente, construídos sobre o **Google ADK (Agent Development Kit)**. O agente raiz (`document_processor`) coordena dois sub-agentes especializados e **três tools determinísticas**, expondo um fluxo end-to-end que transforma documentos de processos de negócio em planilhas Excel e documentos PDF.

```
document_processor  (agente raiz / orquestrador)
├── preparar_state_inicial          (tool local)
├── as_is                           (sub-agente via AgentTool: extração JSON)
├── save_to_firestore_from_state    (tool determinística: JSON do state → Firestore)
├── generate_xlsx_from_state        (tool determinística: JSON → Excel)
├── agente_gerador_pdf_md           (sub-agente via AgentTool: JSON → Markdown)
└── generate_pdf_from_state         (tool determinística: Markdown → PDF)
```

**Modelos LLM utilizados:**
| Agente | Modelo padrão | ThinkingConfig |
|--------|---------------|----------------|
| `document_processor` | `gemini-2.5-flash` (via env `MODEL`) | `thinking_budget=0` (desabilitado) |
| `as_is` | `gemini-2.5-flash` (via env `MODEL`) | Padrão do ADK |
| `agente_gerador_pdf_md` | `gemini-2.5-flash` (hardcoded) | `thinking_budget=8192` |

> **Nota sobre ThinkingConfig:** O `document_processor` usa `thinking_budget=0` para evitar respostas vazias ao processar PDFs com labels de confidencialidade (MSIP). O `agente_gerador_pdf_md` usa `thinking_budget=8192` para gerar Markdown complexo sem consumir todo o budget em pensamento.

---

## 1. `document_processor` — Agente Raiz / Orquestrador

**Arquivo:** `document_processor/agent.py`  
**Nome ADK:** `document_processor`

### Responsabilidade

Atua como ponto de entrada do sistema e especialista sênior em BPM. Recebe arquivos de processos de negócio do usuário (PDF, DOCX, imagens), coordena a execução sequencial dos sub-agentes e das tools determinísticas de geração de artefatos, e retransmite ao usuário as mensagens exatas das ferramentas para ativação dos links de download do ADK.

### Ferramentas (tools)

| # | Ferramenta | Tipo | Arquivo | Descrição |
|---|-----------|------|---------|-----------|
| 1 | `preparar_state_inicial` | Tool local | `document_processor/agent.py` | Lê o arquivo `modelo_documento_processo_instrucoes_v3.md` e salva seu conteúdo em `state["pdf_model_md"]` para ser consumido pelo sub-agente PDF. |
| 2 | `as_is_agent` | `AgentTool` | Encapsula `as_is/agent.py` | Recebe o conteúdo do documento e retorna JSON estruturado N0–N4. O JSON é salvo automaticamente em `state["pdf_input_json"]` via `output_key`. |
| 3 | `save_to_firestore_from_state` | Tool determinística | `document_processor/tools/generate_artifacts.py` | Lê `state["pdf_input_json"]`, extrai o JSON e persiste no Firestore (`as-is-processes`, coleção `processos`). Não depende de LLM. Garante a persistência independentemente do comportamento do sub-agente. |
| 4 | `generate_xlsx_from_state` | Tool determinística | `document_processor/tools/generate_artifacts.py` | Lê `state["pdf_input_json"]`, extrai o JSON, gera o arquivo `processos.xlsx` (via `json_to_xlsx`) e salva como artefato ADK. Não depende de LLM. |
| 5 | `pdf_subagent` | `AgentTool` | Encapsula `agente_gerador_pdf_md/agent.py` | Lê o JSON e o template do state e gera documento Markdown completo. O Markdown é salvo em `state["pdf_markdown"]` via `output_key`. |
| 6 | `generate_pdf_from_state` | Tool determinística | `document_processor/tools/generate_artifacts.py` | Lê `state["pdf_markdown"]`, converte para PDF via ReportLab (`_build_pdf`) e salva como artefato ADK. Não depende de LLM. |

### Fluxo de Execução Obrigatório (6 passos)

```
1. Receber arquivo do usuário
2. Chamar preparar_state_inicial              → carrega template Markdown no state
3. Chamar as_is_agent (com documento)         → gera JSON N0–N4 no state
4. Chamar save_to_firestore_from_state        → persiste JSON no Firestore (determinístico)
5. Chamar generate_xlsx_from_state            → gera processos.xlsx a partir do JSON no state
6. Chamar pdf_subagent (sem args)             → gera Markdown do documento de processo
7. Chamar generate_pdf_from_state             → gera documento_processo.pdf a partir do Markdown no state
8. Retransmitir mensagens LITERAIS das tools generate_xlsx e generate_pdf ao usuário
```

> **Regra crítica:** O agente DEVE chamar as **6 ferramentas** em sequência, mesmo que etapas anteriores devolvam erros aparentes. As tools determinísticas possuem validações próprias e retornam mensagens de erro descritivas.

### State Compartilhado

| Chave no State | Escrito por | Lido por |
|---------------|-------------|----------|
| `pdf_model_md` | `preparar_state_inicial` | `agente_gerador_pdf_md` (via `{pdf_model_md?}` no prompt) |
| `pdf_input_json` | `as_is` (via `output_key`) | `generate_xlsx_from_state`, `agente_gerador_pdf_md` (via `{pdf_input_json?}` no prompt) |
| `pdf_markdown` | `agente_gerador_pdf_md` (via `output_key`) | `generate_pdf_from_state` |

### Guardrails

- Recusa arquivos fora do domínio de processos de negócio (fotos pessoais, receitas etc.).
- Nunca repete dados sensíveis (CPFs, senhas, dados financeiros) fora do escopo técnico.
- Não inventa etapas que não estejam no documento original.
- Não reescreve mensagens das tools (necessário para geração dos links de download).

---

## 2. `as_is` — Sub-agente de Extração Estruturada

**Arquivo:** `as_is/agent.py`  
**Nome ADK:** `as_is`  
**`output_key`:** `pdf_input_json`

### Responsabilidade

Analisa o conteúdo do documento de processo recebido do orquestrador e extrai a estrutura **AS-IS** em formato JSON canônico com hierarquia N0–N4. O JSON resultado é automaticamente salvo no state via `output_key="pdf_input_json"` para consumo posterior.

> **Mudança arquitetural:** O `as_is` **não persiste mais no Firestore nem gera a planilha Excel**. A persistência no Firestore foi movida para a tool determinística `save_to_firestore_from_state` no orquestrador, assim como a geração do XLSX já estava em `generate_xlsx_from_state`. Isso garante desacoplamento total entre extração (LLM) e geração de artefatos (determinístico). A persistência é garantida independentemente do comportamento do modelo LLM no ambiente Vertex AI / Gemini Enterprise.

### Schema JSON gerado

```json
{
  "schemaVersion": "1.0.0",
  "meta": {
    "model": "N0-N4+atributos",
    "createdAt": "2026-02-19T18:04:15-03:00",
    "locale": "pt-BR",
    "output": { "type": "spreadsheet", "recommendedSheetName": "Processos" }
  },
  "columns": [
    { "key": "N0", "label": "Frente (N0)" },
    { "key": "N1", "label": "Macro processo (N1)" },
    { "key": "N2", "label": "Processo (N2)" },
    { "key": "N3", "label": "Tarefa (N3)" },
    { "key": "N4", "label": "Etapa (N4)" },
    { "key": "descricao",             "label": "Descrição (Descrição da etapa)" },
    { "key": "entradas",              "label": "Entradas" },
    { "key": "saidas",                "label": "Saídas" },
    { "key": "sistemasEnvolvidos",    "label": "Sistemas Envolvidos" },
    { "key": "kpis",                  "label": "KPIs" },
    { "key": "oportunidadesMelhoria", "label": "Oportunidades de melhorias" }
  ],
  "rows": [ { "rowId": "...", "N0": "...", "N1": "...", "N2": "...", "N3": "...", "N4": "...",
              "descricao": "...", "entradas": [], "saidas": [], "sistemasEnvolvidos": [],
              "kpis": [], "oportunidadesMelhoria": [], "notes": "...", "tags": [] } ],
  "exportHints": { "arrayJoiner": "\n", "emptyArrayAs": "", "keepColumnOrderAsDefined": true }
}
```

### Ferramentas (tools)

Nenhuma — `tools=[]`. O agente apenas gera o JSON como resposta textual. A persistência no Firestore, a geração do XLSX e do PDF são feitas por tools determinísticas no orquestrador.

### Formato da Resposta Final

O agente retorna na resposta textual **apenas o JSON puro** (começando com `{` e terminando com `}`).

> **Regra crítica:** A resposta **não deve** conter:
> - Markdown code fences (`` ```json ``)
> - Texto explicativo antes ou depois do JSON
> - Mensagens de status ou progresso
>
> O JSON é salvo automaticamente no state via `output_key` e será consumido pelo `save_to_firestore_from_state` (para persistência no Firestore), pelo `generate_xlsx_from_state` (para gerar Excel) e pelo `agente_gerador_pdf_md` (para gerar o documento PDF).

---

## 3. `agente_gerador_pdf_md` — Sub-agente Gerador de Markdown

**Arquivo:** `agente_gerador_pdf_md/agent.py`  
**Nome ADK:** `agente_gerador_pdf_md`  
**`output_key`:** `pdf_markdown`

### Responsabilidade

Lê o JSON do state (`pdf_input_json`) e o modelo de documento (`pdf_model_md`), e gera um documento **completo em Markdown** no formato "AS-IS". O Markdown é salvo automaticamente no state via `output_key="pdf_markdown"`.

> **Mudança arquitetural:** O agente **não gera mais o PDF diretamente**. Ele se limita a gerar o conteúdo Markdown. A conversão Markdown → PDF foi movida para a tool determinística `generate_pdf_from_state` no orquestrador, garantindo separação entre geração de conteúdo (LLM) e renderização (determinístico).

### Entradas via State

| Chave | Conteúdo |
|-------|----------|
| `{pdf_input_json?}` | JSON canônico N0–N4 gerado pelo `as_is` |
| `{pdf_model_md?}` | Template Markdown com instruções e estrutura do documento (`modelo_documento_processo_instrucoes_v2.md`) |

### Prompt

O prompt do agente é composto por duas partes:
1. **Prompt externo** carregado de `prompts/prompt_agente_gerador_pdf.txt` — contém as instruções detalhadas de como interpretar o JSON e gerar cada seção do documento.
2. **Instruções inline** adicionadas em `agent.py` (`ENHANCED_INSTRUCTION`) — contém validações de entrada e regras de formato da resposta final.

### Seções do Documento Gerado

O documento Markdown segue a estrutura:

1. **Metadados do Documento** — processo, área, elaborado por, versão, data, status, classificação, escopo
2. **Descrição Geral do Processo** — nome, objetivo principal, escopo consolidado (início/fim, entradas/saídas)
3. **Fluxograma Simplificado** — diagrama Mermaid (`flowchart TD`) com 5–12 macroetapas
4. **Fluxo Consolidado e Atores** — tabela detalhada por etapa: ator, descrição, entradas, saídas, sistemas, tempo
5. **Diagnóstico AS-IS** — gargalos, problemas, riscos identificados por etapa
6. **Melhorias Possíveis (Backlog Inicial)** — itens de `oportunidadesMelhoria` por N4; itens ausentes marcados como "Sugestão do sub-agent"
7. **KPIs** — extraídos do campo `kpis` de cada etapa
8. **Pendências / Dados Ausentes** — transparência sobre lacunas no JSON de entrada

### Ferramentas (tools)

Nenhuma — `tools=[]`. O agente apenas gera o conteúdo Markdown como resposta textual. A conversão para PDF é feita pela tool `generate_pdf_from_state` no orquestrador.

### Validação de Entrada

Antes de processar, o agente verifica (via instruções no prompt):
1. Se `pdf_input_json` existe no contexto.
2. Se o conteúdo contém um JSON válido (ignora texto antes do primeiro `{` e depois do último `}`).
3. Se o JSON contém os campos obrigatórios: `schemaVersion`, `meta`, `columns`, `rows`.

Caso inválido, retorna mensagem de erro descritiva.

### Regras de Conteúdo

- Documento é **somente AS-IS** — não descreve situação futura (TO-BE).
- Não inclui matriz RACI.
- Não inventa números ou dados ausentes; usa "Não informado".
- A resposta deve ser **exclusivamente** o Markdown completo (sem code fences, sem mensagens de status).

---

## 4. Tools Determinísticas de Geração de Artefatos

**Arquivo:** `document_processor/tools/generate_artifacts.py`

Estas tools são chamadas diretamente pelo `document_processor` **após** os sub-agentes concluírem. Não dependem de LLM — apenas lêem dados do state e geram os arquivos ou persistem dados.

### `save_to_firestore_from_state`

| Aspecto | Detalhe |
|---------|--------|
| **Entrada** | `state["pdf_input_json"]` |
| **Saída** | Documento criado no Firestore (coleção `processos`) |
| **Processamento** | Extrai JSON do texto (tolerante a prefixos/sufixos), valida campos `columns`/`rows`, chama `_write_to_firestore()` via `asyncio.to_thread()`. Usa ADC no Vertex AI (sem `credentials.json`). |
| **Timeout** | 30 segundos |
| **Autenticação** | Prioridade: 1) `credentials.json` local; 2) Application Default Credentials (Vertex AI/Cloud Run) |
| **Dependências** | `google-cloud-firestore` |
| **Motivo da migração** | Removida do sub-agente `as_is` pois o LLM não chama ferramentas de forma garantida no Vertex AI / Gemini Enterprise. |

### `generate_xlsx_from_state`

| Aspecto | Detalhe |
|---------|---------|
| **Entrada** | `state["pdf_input_json"]` |
| **Saída** | Artefato ADK `processos.xlsx` |
| **Processamento** | Extrai JSON do texto (tolerante a prefixos/sufixos), valida campos `columns`/`rows`, chama `json_to_xlsx()` do módulo `agent_tools/json_to_xlsx.py` via `asyncio.to_thread()` |
| **Timeout** | 60 segundos |
| **Dependências** | `pandas`, `openpyxl` |

### `generate_pdf_from_state`

| Aspecto | Detalhe |
|---------|---------|
| **Entrada** | `state["pdf_markdown"]` |
| **Saída** | Artefato ADK `documento_processo.pdf` |
| **Processamento** | Remove code fences se presentes, chama `_build_pdf()` do módulo `agente_gerador_pdf_md/tools/markdown_to_pdf_tool.py` via `asyncio.to_thread()` |
| **Timeout** | 120 segundos |
| **Dependências** | `reportlab` |
| **Fallback** | Se a renderização ReportLab falhar, gera um PDF mínimo contendo a mensagem de erro |

### Função utilitária `_extract_json_from_text`

A tool `generate_xlsx_from_state` utiliza uma função auxiliar que encontra o primeiro `{` e o último `}` no texto do state, extrai o candidato a JSON e valida se contém os campos `columns` e `rows`. Isso torna a pipeline tolerante a sub-agentes que eventualmente incluam texto antes/depois do JSON.

---

## Diagrama de Comunicação entre Agentes

```
Usuário
  │
  ▼
document_processor (orquestrador)
  │
  ├── 1. preparar_state_inicial()
  │       └─→ state["pdf_model_md"] = <template markdown>
  │
  ├── 2. as_is_agent(documento)
  │       ├── LLM analisa o documento
  │       ├── Gera JSON N0–N4
  │       └─→ state["pdf_input_json"] = <JSON gerado>  (via output_key)
  │
  ├── 3. save_to_firestore_from_state()           ← tool determinística
  │       ├── Lê state["pdf_input_json"]
  │       ├── Extrai JSON → _write_to_firestore() via asyncio.to_thread()
  │       └─→ Firestore: coleção 'processos' (doc criado)
  │
  ├── 4. generate_xlsx_from_state()               ← tool determinística
  │       ├── Lê state["pdf_input_json"]
  │       ├── Extrai JSON → json_to_xlsx() → bytes
  │       └─→ ADK artifact: processos.xlsx (versão N)
  │
  ├── 5. pdf_subagent()
  │       ├── Lê state["pdf_input_json"] e state["pdf_model_md"]
  │       ├── LLM gera documento Markdown completo
  │       └─→ state["pdf_markdown"] = <markdown gerado>  (via output_key)
  │
  └── 6. generate_pdf_from_state()                ← tool determinística
          ├── Lê state["pdf_markdown"]
          ├── _build_pdf(markdown) → bytes (ReportLab)
          └─→ ADK artifact: documento_processo.pdf (versão N)
```

---

## Artefatos Gerados

| Arquivo | MIME Type | Gerado por | Download via ADK |
|---------|-----------|-----------|-----------------|
| `processos.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `generate_xlsx_from_state` | ✅ |
| `documento_processo.pdf` | `application/pdf` | `generate_pdf_from_state` | ✅ |

> Os artefatos são versionados automaticamente pelo ADK. Se o mesmo arquivo for gerado novamente na mesma sessão, a versão é incrementada (versão 1, versão 2…). Os artefatos ficam persistidos em `.adk/artifacts/`.

---

## Persistência de Dados (Firestore)

O sub-agente `as_is` persiste cada JSON gerado no **Google Cloud Firestore** através da tool `save_to_firestore_tool`.

| Configuração | Valor |
|-------------|-------|
| Projeto GCP | `steady-computer-487217-p6` |
| Database | `as-is-processes` |
| Coleção | `processos` |
| Credenciais | `credentials.json` (service account na raiz do projeto) |
| Concorrência | Escrita em `asyncio.to_thread()` para não bloquear o event loop |
| Timeout | 30 segundos |
| Client | Singleton (`_firestore_client`) criado na primeira chamada |

---

## Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `GOOGLE_API_KEY` | ✅ | Chave de API do Google AI Studio / Vertex AI |
| `MODEL` | ❌ | Nome do modelo Gemini (padrão: `gemini-2.5-flash`). Não afeta o `agente_gerador_pdf_md`, que usa `gemini-2.5-flash` de forma fixa. |

### Arquivos de Configuração

| Arquivo | Descrição |
|---------|-----------|
| `credentials.json` | Service account do GCP para autenticação no Firestore. Não versionado (deve estar no `.gitignore`). |
| `.env` | Variáveis de ambiente (`GOOGLE_API_KEY`, `MODEL`). Carregado via `python-dotenv`. |

---

## Módulos Utilitários Compartilhados

### `agent_tools/json_to_xlsx.py`

Função `json_to_xlsx(json_path, xlsx_path)` que converte o JSON canônico (com `columns`, `rows`, `exportHints`) em um arquivo Excel formatado:
- DataFrame via `pandas` respeitando a ordem das colunas definida no JSON
- Formatação via `openpyxl`: cabeçalho com fonte branca sobre azul escuro (`#1F4E79`), wrap text, freeze pane na linha 1, autofilter, largura automática de colunas (heurístico com cap em 60 chars)
- Pode ser executado como CLI: `python agent_tools/json_to_xlsx.py <json_path> [xlsx_path]`

### `agente_gerador_pdf_md/tools/markdown_to_pdf_tool.py`

Contém:
- `parse_markdown_to_reportlab(markdown_text)` — parser que converte Markdown em elementos Platypus do ReportLab (headers, parágrafos, tabelas, listas, code blocks, bold)
- `_build_pdf(markdown_content)` — gera bytes do PDF (A4) com fallback para PDF mínimo em caso de erro
- `generate_pdf_tool(markdown_content, tool_context)` — tool ADK legada (não mais usada nas chamadas do pipeline atual; mantida para compatibilidade)

### Arquivos Legados / Não Utilizados no Pipeline Atual

| Arquivo | Status | Descrição |
|---------|--------|-----------|
| `document_processor/tools/create_download_file.py` | Legado | Geração de PDF via `fpdf2`. Não utilizado no pipeline atual. |
| `document_processor/tools/json_to_xlsx.py` | Duplicado | Cópia do `agent_tools/json_to_xlsx.py`. O pipeline atual usa `agent_tools/json_to_xlsx.py`. |
| `as_is/tools/json_to_xlsx_tool.py` | Legado | Tool ADK `generate_xlsx_tool` (gerava XLSX dentro do sub-agente). Substituída por `generate_xlsx_from_state`. |
| `agente_gerador_pdf_md/tools/markdown_to_pdf_tool_fpdf_old.py` | Legado | Implementação anterior usando `fpdf2`. Substituída pela versão ReportLab. |

### Scripts de Debug

| Arquivo | Descrição |
|---------|-----------|
| `dump_session.py` | Inspeciona o banco SQLite de sessões do ADK (tabelas, eventos, schema). |
| `_debug_sessions.py` | Lista sessões recentes e imprime resumo de eventos (tool calls, textos, dados inline). |

---

## Convenções de Desenvolvimento

- **AgentTool:** sub-agentes são encapsulados como `AgentTool` para serem chamados como ferramentas pelo orquestrador, mantendo o escopo do contexto ADK.
- **output_key:** cada sub-agente usa `output_key` para persistir sua saída principal no state compartilhado, desacoplando a comunicação.
- **Separação LLM / artefato:** a geração de conteúdo (JSON, Markdown) é feita pelos sub-agentes via LLM; a geração de artefatos de download (XLSX, PDF) é feita por tools determinísticas no orquestrador. Isso garante que os artefatos sejam sempre gerados, independentemente do comportamento dos sub-agentes.
- **Mensagens literais das tools:** o pipeline depende das mensagens exatas retornadas pelas tools `generate_xlsx_from_state` e `generate_pdf_from_state` para que o ADK processe os links de download. O orquestrador não deve parafrasear essas mensagens.
- **asyncio.to_thread:** operações de I/O síncronas (Firestore, processamento pandas/openpyxl, ReportLab) são executadas em threads separadas para não bloquear o event loop do ADK.
- **Arquivos temporários:** `generate_xlsx_from_state` (e a legada `generate_xlsx_tool`) usam `tempfile.mkdtemp()` e garantem limpeza (`finally`) após leitura dos bytes.
- **Fallback no PDF:** `_build_pdf()` possui bloco `try/except` com fallback que gera um PDF mínimo com a mensagem de erro em caso de falha do ReportLab.
- **Tolerância a ruído no state:** `_extract_json_from_text()` permite que o JSON no state contenha texto antes/depois das chaves, tornando a pipeline resiliente a variações de formato do sub-agente.
