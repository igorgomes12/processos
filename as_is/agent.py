import os

from pathlib import Path
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.genai import types as genai_types
from logger import get_logger
from logger.adk_callbacks import make_before_tool_callback, make_after_tool_callback

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

# No Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=1), GOOGLE_API_KEY conflita com
# project/location e causa ValueError no inicializador do cliente genai.
# Remove a variável para garantir que apenas ADC seja usado.
if os.getenv("GOOGLE_GENAI_USE_VERTEXAI") == "1":
    os.environ.pop("GOOGLE_API_KEY", None)

model_name = os.getenv("MODEL", "gemini-2.5-flash")

# ── Logging ──────────────────────────────────────────────────────
_logger = get_logger()
_before_tool_cb = make_before_tool_callback(_logger)
_after_tool_cb = make_after_tool_callback(_logger, _before_tool_cb)

root_agent = Agent(
    name = "as_is",
    model=model_name,
    description="""Ferramenta para converter processos de negócios em formato de dados estruturados.
    """,
    instruction="""
    ═══════════════════════════════════════════════════════════════════════
    ⚠️  REGRA CRÍTICA: SUA RESPOSTA FINAL DEVE SER APENAS JSON PURO ⚠️
    ═══════════════════════════════════════════════════════════════════════
    
    PROIBIDO:
    ❌ "Aqui está o JSON gerado..."
    ❌ "O mapeamento foi concluído com sucesso. Segue o JSON:"
    ❌ Envolver o JSON em markdown: ```json ... ```
    ❌ Adicionar explicações antes ou depois do JSON
    ❌ Adicionar mensagens de status ou progresso
    
    OBRIGATÓRIO:
    ✅ Sua resposta deve começar com {
    ✅ Sua resposta deve terminar com }
    ✅ Nada mais deve estar presente na resposta
    
    ═══════════════════════════════════════════════════════════════════════
    
    A partir das informações recebidas do agente document_processor, gere um json neste formato:
    {
        "schemaVersion": "1.0.0",
        "meta": {
            "model": "N0-N4+atributos",
            "createdAt": "2026-02-19T18:04:15-03:00",
            "locale": "pt-BR",
            "output": {
            "type": "spreadsheet",
            "recommendedSheetName": "Processos"
            }
        },
        
        "columns": [
            {"key": "N0", "label": "Frente (N0)"},
            {"key": "N1", "label": "Macro processo (N1)"},
            {"key": "N2", "label": "Processo (N2)"},
            {"key": "N3", "label": "Tarefa (N3)"},
            {"key": "N4", "label": "Etapa (N4)"},
            {"key": "descricao", "label": "Descrição (Descrição da etapa)"},
            {"key": "entradas", "label": "Entradas"},
            {"key": "saidas", "label": "Saídas"},
            {"key": "sistemasEnvolvidos", "label": "Sistemas Envolvidos"},
            {"key": "kpis", "label": "KPIs"},
            {"key": "oportunidadesMelhoria", "label": "Oportunidades de melhorias"}
        ],
        
        "rows": [
            {
            "rowId": "string",
            "N0": "string",
            "N1": "string",
            "N2": "string",
            "N3": "string",
            "N4": "string",
        
            "descricao": "string",
        
            "entradas": ["string"],
            "saidas": ["string"],
            "sistemasEnvolvidos": ["string"],
            "kpis": ["string"],
            "oportunidadesMelhoria": ["string"],
        
            "notes": "string",
            "tags": ["string"]
            }
        ],
        
        "exportHints": {
            "arrayJoiner": "\n",
            "emptyArrayAs": "",
            "keepColumnOrderAsDefined": true
        }
        }

    RESTRICAO HIERARQUICA OBRIGATORIA (CARDINALIDADE):
    - O JSON final deve conter EXATAMENTE 1 valor distinto de N0.
    - O JSON final deve conter EXATAMENTE 1 valor distinto de N1.
    - O JSON final deve conter EXATAMENTE 1 valor distinto de N2.
    - Todas as linhas em "rows" devem repetir os mesmos valores de N0, N1 e N2.
    - N3 e N4 podem variar normalmente conforme as tarefas e etapas.
    - Se o documento trouxer mais de uma opcao para N0/N1/N2, consolide em apenas uma hierarquia principal sem inventar dados.

    FLUXO OBRIGATÓRIO:
    1. Gere o JSON estruturado completo seguindo o schema acima.

    2. Valide antes de responder:
       - count_distinct(N0) = 1
       - count_distinct(N1) = 1
       - count_distinct(N2) = 1

    3. RESPOSTA FINAL - Retorne APENAS o JSON puro:
       
    ═══════════════════════════════════════════════════════════════════════
    FORMATO CORRETO DA RESPOSTA:
    ═══════════════════════════════════════════════════════════════════════
    
    {
      "schemaVersion": "1.0.0",
      "meta": { ... },
      "columns": [ ... ],
      "rows": [ ... ]
    }
    
    ═══════════════════════════════════════════════════════════════════════
    
    ATENÇÃO:
    ✅ O JSON puro (começando com { e terminando com })
    ❌ NÃO adicione texto explicativo ou markdown (```json)
    ❌ NÃO adicione mensagens de status antes do JSON
    
    IMPORTANTE: O JSON que você retornar será usado pelo próximo agente (pdf_subagent),
               para gerar a planilha Excel e para persistência no Firestore.
               Retorne SOMENTE o JSON.
    """,
    tools=[],
    output_key="pdf_input_json",
    before_tool_callback=_before_tool_cb,
    after_tool_callback=_after_tool_cb,
    generate_content_config=genai_types.GenerateContentConfig(
        # Com gemini-2.5-flash o thinking mode pode consumir todo o budget
        # e retornar resposta vazia (0 tokens de saída). thinking_budget=0
        # desabilita o pensamento interno, garantindo que o JSON seja gerado.
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    ),
)