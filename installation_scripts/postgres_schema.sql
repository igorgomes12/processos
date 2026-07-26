-- Schema Postgres do grafo ProcessosGraph (db-agente-processo)
-- Equivalente ao schema anterior do Cloud Spanner (N0..N5 + arestas),
-- adaptado para PostgreSQL: UUID nativo, TEXT[] para colunas array,
-- FKs reais em vez de INTERLEAVE, upsert via ON CONFLICT.
--
-- Uso:
--   psql "host=<connection> dbname=db-agente-processo user=postgres" -f postgres_schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS n0_frente (
    id   UUID PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n1_macroprocesso (
    id   UUID PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n2_processo (
    id   UUID PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n3_tarefa (
    id   UUID PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n4_etapa (
    id   UUID PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n5_atributos (
    id                     UUID PRIMARY KEY,
    descricao              TEXT NOT NULL DEFAULT '',
    entradas               TEXT[] NOT NULL DEFAULT '{}',
    saidas                 TEXT[] NOT NULL DEFAULT '{}',
    sistemas_envolvidos    TEXT[] NOT NULL DEFAULT '{}',
    kpis                   TEXT[] NOT NULL DEFAULT '{}',
    oportunidades_melhoria TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edge_has_n1 (
    n0_id UUID NOT NULL REFERENCES n0_frente(id),
    n1_id UUID NOT NULL REFERENCES n1_macroprocesso(id),
    PRIMARY KEY (n0_id, n1_id)
);

CREATE TABLE IF NOT EXISTS edge_has_n2 (
    n1_id UUID NOT NULL REFERENCES n1_macroprocesso(id),
    n2_id UUID NOT NULL REFERENCES n2_processo(id),
    PRIMARY KEY (n1_id, n2_id)
);

CREATE TABLE IF NOT EXISTS edge_has_n3 (
    n2_id UUID NOT NULL REFERENCES n2_processo(id),
    n3_id UUID NOT NULL REFERENCES n3_tarefa(id),
    PRIMARY KEY (n2_id, n3_id)
);

CREATE TABLE IF NOT EXISTS edge_has_n4 (
    n3_id UUID NOT NULL REFERENCES n3_tarefa(id),
    n4_id UUID NOT NULL REFERENCES n4_etapa(id),
    PRIMARY KEY (n3_id, n4_id)
);

CREATE TABLE IF NOT EXISTS edge_has_n5 (
    n4_id UUID NOT NULL REFERENCES n4_etapa(id),
    n5_id UUID NOT NULL REFERENCES n5_atributos(id),
    PRIMARY KEY (n4_id, n5_id)
);

CREATE TABLE IF NOT EXISTS n2_mermaid (
    n2_id          UUID PRIMARY KEY REFERENCES n2_processo(id),
    mermaid_script TEXT NOT NULL,
    gerado_em      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS edge_has_mermaid (
    n2_id      UUID NOT NULL REFERENCES n2_processo(id),
    mermaid_id UUID NOT NULL REFERENCES n2_mermaid(n2_id),
    PRIMARY KEY (n2_id, mermaid_id)
);

CREATE TABLE IF NOT EXISTS n2_tobe_documento (
    n2_id         UUID PRIMARY KEY REFERENCES n2_processo(id),
    tobe_markdown TEXT NOT NULL,
    gerado_em     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS edge_has_tobe (
    n2_id   UUID NOT NULL REFERENCES n2_processo(id),
    tobe_id UUID NOT NULL REFERENCES n2_tobe_documento(n2_id),
    PRIMARY KEY (n2_id, tobe_id)
);

-- Índice para a busca por LIKE/ILIKE em N0_Frente.nome (dataViz/streamlit_grafo.py)
CREATE INDEX IF NOT EXISTS idx_n0_frente_nome ON n0_frente (lower(nome));
