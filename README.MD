# agente-processos

Sistema multi-agente para mapeamento e documentação de processos de negócio no formato **AS-IS**, construído com o **Google ADK (Agent Development Kit)** e modelos **Gemini**. A partir de um documento de processo (PDF, DOCX, imagem), o sistema extrai automaticamente a estrutura hierárquica do processo, persiste os dados no **Google Cloud Firestore** e gera dois artefatos para download: uma **planilha Excel** e um **documento PDF** completo.

---

## Funcionalidades

- **Análise inteligente de documentos** — aceita PDFs, DOCXs e imagens contendo manuais, fluxogramas ou descrições de processos.
- **Extração estruturada AS-IS** — mapeia o processo na hierarquia N0 (Frente) → N1 (Macro processo) → N2 (Processo) → N3 (Tarefa) → N4 (Etapa), com atributos por etapa: descrição, entradas, saídas, sistemas envolvidos, KPIs e oportunidades de melhoria.
- **Persistência no Firestore** — cada JSON gerado é persistido automaticamente no Google Cloud Firestore via tool determinística (projeto `steady-computer-487217-p6`, database `as-is-processes`, coleção `processos`).
- **Geração de planilha Excel** — arquivo `processos.xlsx` formatado com cabeçalho estilizado, wrap text, freeze pane e autofilter.
- **Geração de documento PDF** — arquivo `documento_processo.pdf` (A4) completo com metadados, fluxograma Mermaid, tabela de fluxo, diagnóstico AS-IS, backlog de melhorias, KPIs e registro de pendências.
- **Download via ADK** — ambos os artefatos ficam disponíveis para download diretamente pela interface do ADK.

---

## Pré-requisitos

- Python **3.11.3** (gerenciado via [pyenv-win](https://github.com/pyenv-win/pyenv-win) no Windows)
- Chave de API do Google AI Studio (`GOOGLE_API_KEY`)
- Credenciais de service account GCP (`credentials.json` na raiz do projeto) — necessárias para persistência no Firestore

---

## Instalação

### 1. Clone o repositório

```bash
git clone <url-do-repositorio>
cd agente-processos
```

### 2. Configure o Python 3.11.3 com pyenv-win (Windows)

```powershell
# Instale o pyenv-win caso não tenha
# https://github.com/pyenv-win/pyenv-win#installation

# Instale e ative o Python 3.11.3
pyenv install 3.11.3
pyenv local 3.11.3
```

### 3. Crie e ative o ambiente virtual

```powershell
# Windows (PowerShell)
$env:PYENV_VERSION="3.11.3"
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux/macOS
python3.11 -m venv .venv
source .venv/bin/activate
```

### 4. Instale as dependências

```powershell
pip install -r requirements.txt
```

### 5. Configure as variáveis de ambiente

Crie um arquivo `.env` na raiz:

```dotenv
GOOGLE_API_KEY=sua_chave_aqui
MODEL=gemini-2.5-flash
```

> A variável `MODEL` é opcional. O padrão é `gemini-2.5-flash`. O `agente_gerador_pdf_md` usa `gemini-2.5-flash` de forma fixa, independente desta variável.

### 6. Configure as credenciais do Firestore

Coloque o arquivo `credentials.json` (service account GCP) na raiz do projeto.

> **Importante:** `credentials.json` não deve ser versionado — certifique-se de que esteja no `.gitignore`.

---

## Execução

### Com o ADK Web UI (recomendado)

```powershell
# Com o venv ativado
adk web

# Ou sem ativar o venv
.\.venv\Scripts\python.exe -c "from google.adk.cli import main; main()" web
```

Acesse `http://127.0.0.1:8000` no navegador, selecione o agente **`document_processor`** e envie um documento de processo.

> **Windows (ambientes corporativos):** Se o comando `adk web` retornar "Acesso negado", use o comando alternativo via Python acima. Isso ocorre porque o `adk.exe` gerado pelo pip pode ser bloqueado por políticas de segurança do Windows.

### Com o ADK CLI

```powershell
# Com o venv ativado
adk run document_processor

# Alternativa Windows (se adk.exe bloqueado)
.\.venv\Scripts\python.exe -c "from google.adk.cli import main; main()" run document_processor
```

---

## Uso

1. Inicie o ADK Web UI com `adk web`.
2. Selecione o agente **`document_processor`**.
3. Envie um arquivo contendo a descrição de um processo de negócio (PDF, DOCX ou imagem).
4. O sistema executará automaticamente o pipeline completo (6 etapas):
   1. Preparação do state inicial (carrega template Markdown)
   2. Extração da estrutura AS-IS em JSON (sub-agente `as_is`)
   3. Persistência do JSON no Firestore (tool determinística `save_to_firestore_from_state`)
   4. Geração de `processos.xlsx` a partir do JSON (tool determinística `generate_xlsx_from_state`)
   5. Geração do Markdown do documento de processo (sub-agente `agente_gerador_pdf_md`)
   6. Geração de `documento_processo.pdf` a partir do Markdown (tool determinística `generate_pdf_from_state`)
5. Os links de download para os dois arquivos aparecerão na interface ao final do processamento.

---

## Estrutura do Projeto

```
agente-processos/
│
├── document_processor/           # Agente raiz / orquestrador
│   ├── __init__.py
│   ├── agent.py                  # Definição do agente, tool preparar_state_inicial e AgentTools
│   ├── data-models/
│   │   ├── modelo_documento_processo_instrucoes_v2.md  # Template do documento AS-IS (v2)
│   │   └── modelo_documento_processo_instrucoes_v3.md  # Template do documento AS-IS (v3, ativo)
│   └── tools/
│       ├── generate_artifacts.py     # Tools determinísticas: save_to_firestore_from_state,
│       │                             #   generate_xlsx_from_state, generate_pdf_from_state
│       └── json_to_xlsx.py           # (legado) Cópia duplicada do agent_tools/json_to_xlsx.py
│
├── as_is/                        # Sub-agente: extração JSON N0–N4 (apenas LLM, sem tools)
│   ├── __init__.py
│   ├── agent.py                  # Agente as_is com output_key="pdf_input_json", tools=[]
│   └── tools/
│       ├── __init__.py
│       ├── firestore_tool.py         # (legado) Tool de persistência — substituída por save_to_firestore_from_state
│       └── json_to_xlsx_tool.py      # (legado) Tool antiga de geração XLSX dentro do sub-agente
│
├── agente_gerador_pdf_md/        # Sub-agente: gera Markdown do documento (apenas LLM, sem tools)
│   ├── __init__.py
│   ├── agent.py                  # Agente pdf com output_key="pdf_markdown", tools=[]
│   └── tools/
│       ├── __init__.py
│       └── markdown_to_pdf_tool.py   # Parser Markdown→ReportLab + _build_pdf()
│
├── agent_tools/
│   └── json_to_xlsx.py           # Função utilitária compartilhada: json_to_xlsx()
│
├── logger/                       # Sistema de logging estruturado
│   ├── __init__.py
│   ├── adk_callbacks.py          # Callbacks do ADK para logging de eventos
│   ├── base.py                   # Interface base do logger
│   ├── cloud_logger.py           # Logger para Google Cloud Logging
│   ├── composite_logger.py       # Logger composto (local + cloud)
│   ├── factory.py                # Fábrica de loggers
│   └── local_logger.py           # Logger local (arquivo/console)
│
├── prompts/
│   └── prompt_agente_gerador_pdf.txt  # Prompt detalhado do sub-agente gerador de Markdown
│
├── tests/                        # Testes automatizados
│   ├── conftest.py
│   ├── test_agent_tools/
│   ├── test_agente_gerador_pdf_md/
│   ├── test_as_is/
│   ├── test_document_processor/
│   └── test_logger/
│
├── .venv/                        # Ambiente virtual Python 3.11.3 (não versionado)
├── .python-version               # Versão do Python para pyenv (3.11.3)
├── credentials.json              # Service account GCP (não versionado)
├── .env                          # Variáveis de ambiente (não versionado)
├── json_example.py               # Exemplo do schema JSON canônico
├── requirements.txt              # Dependências Python
├── deploy.py                     # Script de deploy
├── AGENTS.MD                     # Documentação técnica dos agentes
└── README.MD                     # Este arquivo
```

---

## Arquitetura de Agentes

O sistema usa **3 agentes** organizados em hierarquia orquestradora/sub-agente, com **3 tools determinísticas** para persistência e geração dos artefatos de download.

```
document_processor  ← agente raiz (orquestrador) [gemini-2.5-flash, thinking_budget=0]
├── preparar_state_inicial          ← tool local: carrega template Markdown no state
├── as_is                           ← sub-agente [gemini-2.5-flash]: extrai JSON N0–N4
│                                      └─→ state["pdf_input_json"]  (via output_key)
├── save_to_firestore_from_state    ← tool determinística: JSON → Firestore
├── generate_xlsx_from_state        ← tool determinística: JSON → processos.xlsx
├── agente_gerador_pdf_md           ← sub-agente [gemini-2.5-flash, thinking_budget=8192]: JSON → Markdown
│                                      └─→ state["pdf_markdown"]  (via output_key)
└── generate_pdf_from_state         ← tool determinística: Markdown → documento_processo.pdf
```

**Princípio arquitetural:** A geração de conteúdo (JSON, Markdown) é feita pelos sub-agentes via LLM. A persistência no Firestore e a geração de artefatos de download (XLSX, PDF) são feitas por **tools determinísticas** no orquestrador, garantindo que os arquivos sejam sempre criados independentemente do comportamento dos sub-agentes.

Consulte [AGENTS.MD](AGENTS.MD) para a documentação completa da arquitetura, responsabilidades, tools, state compartilhado e convenções de cada agente.

---

## Schema JSON Canônico (N0–N4)

O sistema usa um schema padronizado para representar processos. Cada linha (`row`) representa uma **etapa N4**:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `N0` | string | Frente (ex.: "Operações") |
| `N1` | string | Macro processo (ex.: "Gestão de Contratos") |
| `N2` | string | Processo (ex.: "Renovação de Contrato") |
| `N3` | string | Tarefa / agrupador |
| `N4` | string | Etapa executável |
| `descricao` | string | Descrição detalhada da etapa |
| `entradas` | string[] | Insumos/dados de entrada |
| `saidas` | string[] | Produtos/entregáveis de saída |
| `sistemasEnvolvidos` | string[] | Sistemas/ferramentas usados |
| `kpis` | string[] | Indicadores de desempenho |
| `oportunidadesMelhoria` | string[] | Melhorias identificadas na etapa |

Consulte [json_example.py](json_example.py) para um exemplo completo do schema.

---

## Artefatos Gerados

| Arquivo | Formato | Gerado por | Conteúdo |
|---------|---------|-----------|----------|
| `processos.xlsx` | Excel (.xlsx) | `generate_xlsx_from_state` | Planilha com todas as etapas N4, cabeçalho estilizado (azul escuro `#1F4E79`), filtros automáticos e texto com quebra de linha |
| `documento_processo.pdf` | PDF (A4) | `generate_pdf_from_state` | Documento completo AS-IS: metadados, fluxograma, tabela de fluxo, diagnóstico, backlog de melhorias, KPIs, pendências |

> Os artefatos são versionados automaticamente pelo ADK. Se o mesmo arquivo for gerado novamente na mesma sessão, a versão é incrementada.

---

## Dependências Principais

| Pacote | Versão instalada | Uso |
|--------|-----------------|-----|
| `google-adk` | 1.26.0 | Framework de agentes (ADK) |
| `google-genai` | latest | SDK do Gemini |
| `google-cloud-aiplatform` | 1.139.0 | Integração com Vertex AI / AI Platform |
| `google-cloud-firestore` | latest | Persistência dos dados no Firestore |
| `google-cloud-logging` | latest | Logging estruturado no Google Cloud |
| `reportlab` | latest | Geração de PDF a partir de Markdown |
| `pandas` | latest | Manipulação de dados para Excel |
| `openpyxl` | latest | Formatação avançada do Excel |
| `python-dotenv` | latest | Carregamento de variáveis de ambiente |

---

## Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `GOOGLE_API_KEY` | Sim | Chave de API do Google AI Studio / Vertex AI |
| `MODEL` | Não | Nome do modelo Gemini (padrão: `gemini-2.5-flash`). Não afeta o `agente_gerador_pdf_md`, que usa `gemini-2.5-flash` de forma fixa. |

---

## Limitações Conhecidas

- O fluxograma Mermaid gerado no PDF é representado como texto (o ReportLab não renderiza diagramas Mermaid nativamente).
- Documentos com estrutura muito informal ou sem hierarquia clara podem resultar em mapeamentos imprecisos — recomenda-se revisar o JSON gerado antes de usar o PDF como documento oficial.
- A variável `MODEL` não afeta o `agente_gerador_pdf_md`, que usa `gemini-2.5-flash` de forma fixa.
- O `document_processor` usa `thinking_budget=0` para evitar respostas vazias ao processar PDFs com labels de confidencialidade (MSIP).
- O extra `agent_engines` do `google-cloud-aiplatform` não está disponível na versão 1.139.0 — isso gera um warning durante a instalação, mas não impede o funcionamento do sistema.
- No Windows com pyenv-win, o comando `python -m venv` pode falhar via shim; use `$env:PYENV_VERSION="3.11.3"` antes de criar o venv ou use o caminho direto do executável.

---

## Contribuindo

1. Faça um fork do repositório.
2. Crie uma branch descritiva: `git checkout -b feat/minha-melhoria`.
3. Implemente e teste suas alterações.
4. Abra um Pull Request com descrição clara do que foi alterado e por quê.
