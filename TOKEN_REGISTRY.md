# CP-TOKEN-REGISTRY-01 — registro persistente de tokens

> Atualização 10/09/2026: a agenda persistente foi implantada nas etapas 3.1–3.4. Política e estado atuais em [TOKEN_TRACKING.md](TOKEN_TRACKING.md); a descrição abaixo registra o checkpoint anterior.

Concluído e implantado em 09/09/2026, A0, sem subagentes. Normalizador v4, contratos econômicos e perfil de coleta preservados.

## Entrega e limites

O registro conserva uma identidade por `source_kind + chain + token_address`, com captura de origem, versão da interpretação, pointer do item e horário de registro. Endereços EVM conhecidos são normalizados; Solana preserva caixa. A fonte separa fixtures, GMGN e evidência legada. Identidade observada não é confirmação on-chain.

A primeira referência registrada permanece imutável quando o token sai/reentra no ranking ou reaparece em outra captura. `registered_at` é o instante da publicação no registro; `registration_capture_received_at` vem da captura escolhida. Nenhum deles afirma o nascimento do token ou a primeira observação de todo o histórico. Se uma captura atrasar por falha, outra pode registrar a identidade primeiro.

A migration 009 é aditiva e marca o início em **2026-09-09T22:25:08.487691Z**. Somente capturas aceitas desde esse marco, com interpretação publicada `gmgn-p0-v4`, participam. Não houve backfill, exclusão ou reinterpretação histórica. Os fatos continuam nos destinos existentes; o registro não inventa fatos econômicos.

A extração usa os campos de identidade aprovados no catálogo da interpretação e o bruto com SHA256 verificado. Isso inclui tokens que só possuem endereço, sem preço/supply. Endereços de dev/wallets e capturas com identidade conflitante não são promovidos. Contratos incertos de preço/market cap não impedem o registro de uma identidade válida.

## Persistência e consulta

Etapa independente `registry` no worker, até duas capturas por rodada. Falha conserva tentativa, código e retry de 30 s; a captura é adiada para permitir avanço das seguintes. Inserções e conclusão do lote compartilham transação e a barreira temporal do banco. Replay não duplica nem troca a referência original. Mecanismos confrontados com [INSERT/ON CONFLICT](https://www.postgresql.org/docs/18/sql-insert.html) e [advisory locks](https://www.postgresql.org/docs/18/explicit-locking.html).

`GET /v1/tokens` exige `source_kind`, `chain` e `knowledge_cutoff` UTC; aceita filtro exato `token`, limite 1–200 e cursor `after_registry_id`. Retorna proveniência, início de cobertura, versão, próxima página e `tracking_enabled=false`. Use o mesmo corte nas páginas. A resposta não afirma presença atual no ranking.

`GET /v1/status` inclui `token_registry`: contagens por fonte/chain, pendências/erros/idade, heartbeat e `current`. O resumo anterior do pipeline continua descrevendo normalização/destinos; a atualização do registro tem status separado.

Política deste recorte: registros sem expiração automática, nenhum agendamento/prioridade de acompanhamento ativado. Frequência GMGN continua três jobs/60 s. Retenção de dados brutos e capacidade de longo prazo seguem sujeitas ao planejamento existente.

## Validação

- **125 testes aprovados**, zero falhas/erros/skips, 2,788 s. Incluem 12 casos novos do registro (três integrações PostgreSQL em schemas exclusivos), regressões do pipeline, persistência, valuation e contexto.
- Provados: identidade sem métrica econômica, duplicação, separação de fontes/chain/caixa, saída/reentrada, retomada, isolamento de corte/paginação, captura ainda não publicada e exclusão de histórico anterior ao marco.
- Falha induzida na conclusão do lote reverteu os registros; retry persistiu e outras capturas avançaram. Ausência do bruto também manteve retry. Schemas temporários removidos.
- Prova real: identidades originadas em Trending e Trenches conferidas nos respectivos brutos/SHA256 e pointers; recibos armazenados de Lake/ClickHouse aprovados. Não foi uma nova varredura física integral dos destinos.
- **Restart real do worker:** todos os **241 registros** existentes no corte anterior tiveram contagem e SHA256 do conteúdo integral preservados.
- Amostra final: **274 tokens GMGN registrados; 567 capturas GMGN e 22 fixtures prontas; seis serviços saudáveis; filas do registro, normalização, destinos e coletor zeradas.** Coletor sem falhas ou intervalos perdidos nos contadores, mesmo container desde 19:24:44 UTC.
- Primeiro probe exigiu fila vazia durante uma captura em trânsito (0,177 s de idade). Ajustado apenas o probe para aguardar até seis segundos pela drenagem; a repetição passou, sem mudança na aplicação.
- Suíte completa, janela longa e capacidade diária não foram executadas/certificadas neste recorte.

API/worker: `midas-data-app:registry-v1-20260909`, SHA256 `bb1122fe9b17b31d8c3d9c41abd8bb4bcf50db24cd8f5b3a4ab2a5326e3c1451`. Migration 009 com checksum conferido. Tag persistida no arquivo privado da base; demais serviços preservados.

Evidências: `.scratch/checkpoint-token-registry-20260909/`, incluindo `focused.xml`, build/deploy, `live-result.json`, `final-live-result.json`, `restart-before.json`, `restart-after.json` e inventários de serviços.

## Próximo recorte

CP-TOKEN-TRACKING-01: fila persistente de acompanhamento por token, com seleção, prioridade e vencimento explícitos, dentro do orçamento atual. O registro concluído é a entrada desse trabalho; acompanhamento contínuo de todos os tokens, curvas/marcos e estratégias permanecem posteriores.
