# Documento de Processo — (NOME DO PROCESSO)
> **Propósito deste documento:** descrever o processo “As Is” (como é hoje) com clareza operacional, evidenciar gargalos/ineficiências e registrar KPIs e informações de contexto para diagnóstico e melhorias.
 
## Metadados do Documento (obrigatório)
- **Processo:** (Nome oficial e/ou nome usado internamente)
- **Área dona (Process Owner):** (Área responsável e nome do responsável, se aplicável)
- **Elaborado por:** (Nome / time)
- **Revisado por:** (Nome / time)
- **Versão:** (ex.: v1.0)
- **Data:** (dd/mm/aaaa)
- **Status:** (Rascunho | Em revisão | Aprovado)
- **Classificação da informação:** (Público | Interno | Restrito | Confidencial)
- **Escopo do levantamento:** (As Is) — **este modelo é somente As Is**
- **Fontes consultadas:** (entrevistas, políticas, tickets, sistemas, relatórios etc.)
 
> **Instrução ao sub-agent:** preencha metadados primeiro. Se algum item não existir, escreva “Não disponível” e indique o motivo (ex.: “Process Owner não definido”).
 
---
 
## 1. Descrição Geral do Processo
> Esta seção deve permitir que qualquer pessoa entenda **o que é o processo**, **por que existe**, **onde começa/termina** e **o que entra/saí**.
 
### 1.1 Nome do Processo
- **Nome do Processo:** (preencher)
 
**O que incluir:**
- Nome padronizado (o mais usado na organização).
- Eventuais sinônimos (apelidos internos), se houver.
 
### 1.2 Objetivo Principal
- **Objetivo Principal:**  
  - O propósito fundamental do processo é (...).  
  - O processo visa (...).
 
**O que incluir (obrigatório):**
- **Finalidade de negócio:** por que esse processo existe?
- **Resultado esperado:** qual valor entrega (ex.: resposta ao cliente, conformidade regulatória, redução de risco)?
- **Critérios de sucesso:** como “bom” é medido (ex.: SLA, qualidade, satisfação, compliance).
 
**Como escrever (boa prática):**
- Use frases objetivas e orientadas a resultado.
- Evite descrição de “como faz” (isso vai para o fluxo).
 
### 1.3 Escopo Consolidado
- **Início:** O processo se inicia com (...).  
- **Fim:** O processo termina com (...).  
- **Entradas do Processo:**  
  - (...)  
- **Saídas do Processo:**  
  - (...)
 
**O que incluir (obrigatório):**
- **Evento de início (trigger):** o que dispara o processo (ex.: “recebimento de demanda”).
- **Condição de término:** o que caracteriza encerramento (ex.: “resposta enviada + caso encerrado no sistema”).
- **Entradas:** dados/documentos/canais que alimentam (ex.: reclamação, formulário, e-mail, API).
- **Saídas:** entregáveis gerados (ex.: resposta formal, registro em sistema, evidências, relatórios).
 
**Regras e limites (obrigatório):**
- **O que está dentro do escopo:** inclua o que o processo cobre.
- **O que está fora do escopo:** declare explicitamente (ex.: “análises de fraude fora do fluxo”; “tratativas pós-encerramento”).
 
---
 
## 2. Fluxograma Simplificado do Processo
> Objetivo: apresentar uma visão rápida do fluxo ponta-a-ponta, com poucas caixas (macroetapas).
 
### 2.1 Orientações para o desenho
**O fluxograma deve:**
- Conter de **5 a 12 blocos** (macroetapas).
- Indicar **início e fim**.
- Marcar **pontos de decisão** (se aplicável) com “Sim/Não”.
- Indicar **principais handoffs** (transferências entre áreas/fornecedores) quando relevantes.
 
**Pode ser fornecido como:**
- **Imagem/diagrama** (cole abaixo), **OU**
- **Mermaid** (preferencial para versionamento em Git).
 
### 2.2 Mermaid (modelo)
> **Instrução ao sub-agent:** adapte nomes das etapas ao processo real e mantenha o fluxo coerente com a tabela da seção 3.
 
```mermaid
flowchart TD
  A[Início: (gatilho do processo)] --> B[Etapa 1: (macroetapa)]
  B --> C{Decisão? (se houver)}
  C -- Sim --> D[Etapa 2: (macroetapa)]
  C -- Não --> E[Etapa alternativa / exceção]
  D --> F[Fim: (condição de encerramento)]
  E --> F
```
 
---
 
## 3. Fluxo Consolidado e Atores
> Esta é a seção mais importante para operacionalizar o “As Is”.
> Ela detalha as etapas, atividades, atores e sistemas.
 
### 3.1 Instruções de preenchimento (obrigatório)
Para **cada etapa**, o sub-agent deve preencher:
 
1) **Etapa (nome curto):** ex.: “Triagem e Direcionamento”  
2) **Principais atividades (detalhado):**
   - Liste as ações em ordem (verbos no infinitivo: “validar”, “registrar”, “encaminhar”…).
   - Indique se é **manual, semiautomático ou automático**.
   - Cite **critérios de decisão** (ex.: “se duplicado, então…”).
3) **Principais atores (áreas/cargos):**
   - Separe em **Externos** (fornecedores, parceiros) e **Internos** (áreas, squads).
   - Se houver, indique **nível** (N1/N2/N3) ou papel (operador, analista, aprovador).
4) **Principais sistemas utilizados:**
   - Nome do sistema (ex.: Salesforce).
   - Para cada sistema, diga **para quê** é usado (ex.: “abrir caso”, “anexar evidências”).
5) **Entradas/saídas por etapa (recomendado):**
   - Qual informação/documento entra na etapa e o que sai.
6) **Tempo/SLA da etapa (se existir):**
   - Prazos regulatórios, internos e prazos “de fato” (média).
7) **Evidências (se aplicável):**
   - Prints, logs, e-mails, IDs de casos, relatórios.
 
### 3.2 Tabela do fluxo (preencher tudo)
> **Instrução ao sub-agent:** adicione quantas linhas forem necessárias. Cada linha deve ser uma etapa “atômica” o bastante para identificar gargalos.
 
| Etapa | Principais Atividades | Principais Atores (Áreas/Cargos) | Principais Sistemas Utilizados |
|------:|------------------------|-----------------------------------|--------------------------------|
| 1. (Nome da etapa) | (Descreva atividades em bullets dentro da célula) | **Externos:** (...)<br>**Internos:** (...) | (...) |
| 2. (Nome da etapa) | (...) | **Externos:** (...)<br>**Internos:** (...) | (...) |
| 3. (Nome da etapa) | (...) | **Externos:** (...)<br>**Internos:** (...) | (...) |
 
#### 3.3 Observações (opcional, mas recomendado)
> Use este bloco para capturar contexto que não cabe bem na tabela.
 
- **Regras/Políticas aplicáveis:** (normas internas, regulações, políticas de atendimento, compliance)
- **Exceções e variações do fluxo:** (ex.: canal X tem regra diferente; cliente PF vs PJ)
- **Dependências:** (áreas que precisam responder; integrações; fornecedores; janelas de batch)
- **Pontos de controle/aprovação:** (quem aprova, critérios, quando ocorre)
- **Riscos percebidos:** (ex.: risco regulatório por SLA, risco de erro manual)
 
---
 
## 4. Diagnóstico do Processo "As Is"
> Objetivo: consolidar problemas, causas e impactos.
> Esta seção deve ser baseada em evidências do fluxo e em sintomas observáveis.
 
### 4.1 Principais pontos críticos e ineficiências (obrigatório)
Liste de **3 a 10** achados, cada um com:
- **Título curto do problema** (ex.: “Alta dependência de atividades manuais”)
- **Descrição do que acontece hoje** (o “sintoma”)
- **Onde ocorre (etapa)** (referencie a tabela da seção 3)
- **Evidências** (ex.: volume, prints, relatos, exemplos, IDs)
- **Frequência** (sempre, recorrente, pontual)
- **Severidade** (Alta/Média/Baixa)
 
**Formato sugerido (copie e replique):**
1) **(Problema #1 — título)**
   - **Sintoma:** (...)
   - **Etapa(s):** (Etapa X, Y)
   - **Evidências:** (...)
   - **Frequência:** (...)
   - **Severidade:** (...)
 
2) **(Problema #2 — título)**
   - ...
 
### 4.2 Causas prováveis (opcional, recomendado)
> **Instrução ao sub-agent:** se não houver confirmação, use linguagem de hipótese (“provável”, “sugere-se”).
 
- (Causa #1: tecnologia/integração)
- (Causa #2: processo/regra)
- (Causa #3: pessoas/capacitação)
- (Causa #4: governança/SLA interno)
 
### 4.3 Impactos (opcional, recomendado)
Explicite impactos em:
- **Tempo:** aumento de TMT, atrasos, retrabalho
- **Custo:** horas manuais, custo de fornecedor, multas
- **Qualidade:** erros, inconsistência, perda de informação
- **Risco:** regulatório, jurídico, reputacional
- **Experiência do cliente:** fricção, baixa nota, reincidência
 
---
 
## 5. Melhorias Possíveis (Backlog Inicial)
> **Objetivo:** registrar **oportunidades de melhoria** a partir do diagnóstico “As Is”, sem desenhar o processo “To Be” completo.
 
### 5.1 Como preencher (obrigatório)
> **Instrução ao sub-agent:** preencha esta seção **apenas** com melhorias plausíveis e diretamente conectadas aos problemas descritos na seção 4.
 
Para cada melhoria, informe:
- **Etapa do fluxo (referência):** cite a(s) etapa(s) da seção 3 onde a melhoria se aplica.
- **Problema associado:** referencie o achado da seção 4 (ex.: “Problema #2”).
- **Descrição da melhoria:** o que mudar (ação concreta), evitando generalidades.
- **Tipo:** (Automação | Integração | Padronização | Governança/SLA | Dados | Treinamento | Outros)
- **Ganho esperado:** (tempo, custo, qualidade, risco, experiência do cliente) — seja específico.
- **Esforço estimado (qualitativo):** (Baixo | Médio | Alto) e por quê.
- **Dependências/Riscos:** (sistemas, fornecedor, aprovações, compliance).
- **Evidências/observações:** dados, exemplos, incidentes ou notas que sustentam a proposta.
 
### 5.2 Lista estruturada de melhorias (preencher tudo)
> **Instrução ao sub-agent:** use uma linha por melhoria. Adicione quantas linhas forem necessárias.
 
| # | Etapa(s) (Seção 3) | Problema (Seção 4) | Melhoria proposta | Tipo | Ganho esperado | Esforço (B/M/A) | Dependências/Riscos | Evidências/Observações |
|---:|--------------------|--------------------|------------------|------|---------------|-----------------|---------------------|----------------------|
| 1 | (Etapa X) | (Problema #_) | (...) | (...) | (...) | (...) | (...) | (...) |
| 2 | (Etapa Y) | (Problema #_) | (...) | (...) | (...) | (...) | (...) | (...) |
 
### 5.3 Priorização rápida (opcional, recomendado)
> **Instrução ao sub-agent:** se houver base suficiente, faça uma priorização simples.
 
- **Critério sugerido:** Impacto (1–5) x Esforço (1–5) x Urgência (1–5)
- **Top 3 melhorias recomendadas:**
  1. (Melhoria #...)
  2. (Melhoria #...)
  3. (Melhoria #...)
 
---
 
## 6. KPIs e Informações Adicionais
> Objetivo: registrar como o processo é medido e quais dados suportam gestão.
 
### 6.1 Principais KPIs (obrigatório)
Para cada KPI, preencher:
- **Nome do KPI**
- **Definição (o que mede)**
- **Fórmula (quando aplicável)**
- **Fonte de dados** (sistema/relatório)
- **Frequência de medição** (diária/semanal/mensal)
- **Meta/threshold** (se existir)
- **Dono do indicador** (área/papel)
- **Observações** (limitações, dados faltantes, vieses)
 
**Modelo:**
- **KPI 1 — (Nome):**
  - **Definição:** (...)
  - **Fórmula:** (...)
  - **Fonte:** (...)
  - **Frequência:** (...)
  - **Meta:** (...)
  - **Dono:** (...)
  - **Observações:** (...)
 
- **KPI 2 — (Nome):**
  - ...
 
### 6.2 Informações adicionais (recomendado)
> Use para contexto operacional que afeta performance.
 
- **Volumetria:** (por canal, por tipo de demanda, por período)
- **Sazonalidade:** (picos conhecidos e por quê)
- **Capacidade/Headcount:** (equipes envolvidas, turnos, fornecedores)
- **SLAs internos:** (quando existem e como são cobrados)
- **Integrações/automação:** (APIs, RPAs, jobs; estabilidade; incidentes recorrentes)
- **Qualidade de dados:** (campos obrigatórios, inconsistências, duplicidades)
 
### 6.3 Esforço e Atividades Manuais (obrigatório quando houver manualidade)
> **Instrução ao sub-agent:** seja específico: “o que é manual” + “por que” + “impacto”.
 
- **Atividade manual 1:** (o que é feito manualmente e em qual etapa)
  - **Motivo:** (ex.: falta de integração; regra de negócio)
  - **Impacto:** (tempo, erro, custo)
  - **Volume afetado:** (se possível)
 
- **Atividade manual 2:** (...)
 
---
 
## Anexos (opcional)
> Inclua quando houver material de suporte útil para auditoria, entendimento ou replicação.
 
### Anexo A — Glossário (recomendado)
- **Termo 1:** definição
- **Termo 2:** definição
 
### Anexo B — Referências/Links (recomendado)
- (link para policy, manual, runbook, dashboard, páginas internas)
 
### Anexo C — Evidências (prints, logs, etc.)
- Evidência 1: (descrição + onde encontrar + data)
- Evidência 2: (...)
 
---
 
## Checklist de Qualidade (obrigatório antes de finalizar)
> O sub-agent deve validar este checklist e marcar como OK/Não OK:
 
- [ ] O processo tem **início e fim** claros (seção 1.3).
- [ ] Entradas e saídas estão completas e coerentes com o fluxo.
- [ ] Fluxograma (seção 2) condiz com a tabela (seção 3).
- [ ] Cada etapa tem ator(es) e sistema(s) associados.
- [ ] Diagnóstico contém **evidências** e referencia etapas do fluxo.
- [ ] A seção de **Melhorias Possíveis** referencia etapa(s) e problema(s) diagnosticados.
- [ ] KPIs têm **definição + fonte + frequência** (mínimo).
- [ ] Manualidades (se existirem) estão descritas com impacto.
- [ ] Classificação da informação preenchida.
- [ ] Metadados completos (versão, data, autores).
 
> **Fim do modelo**
 