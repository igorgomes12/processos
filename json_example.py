json_example = """
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
"""