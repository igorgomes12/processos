# Visão Geral — agente-processos

Resumo enxuto do sistema em produção, para orientação rápida. Detalhes técnicos completos: `README.MD` (arquitetura, banco, pipeline) e `AGENTS.MD` (cada agente). **Atenção:** essas duas docs têm trechos desatualizados — ver seção "Docs desatualizadas" no final.

---

## O que o sistema faz

Recebe um documento de processo de negócio (PDF/DOCX/imagem), usa Gemini para extrair a estrutura AS-IS (hierarquia N0 Frente → N1 Macroprocesso → N2 Processo → N3 Tarefa → N4 Etapa → N5 Atributos), gera Excel + PDF + diagrama Mermaid, e depois — mediante aprovação do usuário — gera uma proposta TO-BE. Tudo fica persistido no Postgres e é consultável por um dashboard Streamlit.

## Arquitetura do pipeline (2 turnos)

```
Turno 1 (Fase 1)                          Turno 2 (Fase 2, após "ok"/"aprovado")
──────────────────                        ─────────────────────────────────────
1. Extração AS-IS (LLM direto)            5. Markdown TO-BE (LLM direto)
2. Grava no Postgres                      6. PDF TO-BE + grava Postgres
3. Markdown + XLSX + PDF AS-IS
4. Mermaid → Postgres (best-effort)
   ↓
   pausa e pede aprovação ao usuário
```

Por que 2 turnos: um turno único (~10 chamadas LLM, ~5min) travava intermitentemente em produção no Vertex AI Agent Engine. Dividir em 2 turnos curtos, gerando conteúdo via chamada direta ao `google-genai` (sem sub-agente ADK aninhado), resolveu — ver `[[agente_processos_chat_hang_incident]]` na memória do projeto.

**Firestore foi removido do pipeline** (era escrita redundante, nunca lida de volta, e contribuía para os travamentos). Fonte única de dados hoje é o Postgres.

## Onde cada peça roda

| Peça | Onde | Status |
|---|---|---|
| Agente (pipeline acima) | Vertex AI Agent Engine (Reasoning Engine), `us-central1` | Ativo: `reasoningEngines/4419730529671184384` |
| Chat para usuário final | Gemini Enterprise / Agentspace, agent card "Agente de Processos" | Precisa de licença Gemini Enterprise por usuário (checar validade — ver memória) |
| Dashboard (dataViz) | Cloud Run, serviço `streamlit-processos`, `us-central1` | **No ar hoje**: https://streamlit-processos-3x37dadngq-uc.a.run.app |
| Banco de dados | Cloud SQL PostgreSQL, instância `agente-processos-db`, `us-east1`, database `db-agente-processo` | Ativo |

Redeploy do dashboard: `python deploy_streamlit.py` (rebuild + deploy, ~2min). Redeploy do agente: `python deploy.py` — **cria um Reasoning Engine novo a cada execução**; depois é preciso reapontar manualmente o agent card no Gemini Enterprise para o novo `resource_name`.

## Banco de dados

8 tabelas de hierarquia/artefato (`n0_frente` … `n5_atributos`, `n2_mermaid`, `n2_tobe_documento`) + 7 tabelas de aresta (`edge_has_*`), todas em `db-agente-processo`. Schema completo em `installation_scripts/postgres_schema.sql`.

## O que foi corrigido nesta sessão

1. **Dashboard nunca tinha sido publicado** — feito o primeiro deploy no Cloud Run.
2. **Senha do Postgres desalinhada** — o `.env` já tinha a senha certa, mas o Cloud SQL estava com outra; resetada para bater. Isso também pode ter feito o agente falhar silenciosamente ao gravar processos antes do fix.
3. **Tema do dashboard seguia dark-mode do navegador** — não havia `.streamlit/config.toml`; widgets nativos (expander) e o diagrama Mermaid ficavam ilegíveis (texto claro sobre fundo escuro) para quem usa o SO/navegador em modo escuro. Fixado tema claro explícito + CSS de reforço para o Mermaid.

## Problemas conhecidos (não corrigidos ainda)

- **Acentuação quebrada nos dados**: nomes salvos no Postgres aparecem como `Opera��es`, `Governan�a` — mismatch de charset na escrita ou leitura. Não impede uso, mas deixa texto ilegível.
- **README.MD / AGENTS.MD desatualizados**: descrevem pipeline de turno único com Firestore e render do Mermaid via `mermaid.ink`/CDN — nenhum dos dois reflete a arquitetura atual (2 turnos, sem Firestore, `st.mermaid_chart()` nativo). Vale uma atualização se for usado por alguém novo no time.
- **Licença Gemini Enterprise**: registrada como expirada em memória de Ago/2026 — sem ela, usuários finais não acessam o agente via chat (o dashboard funciona independente disso).
