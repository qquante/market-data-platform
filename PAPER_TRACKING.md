# 5.3.4.1 — posições paper na fila por token

> Continuidade: [5.3.4.2 concluída](PAPER_PRICES.md). O token_info já foi consumido pelo paper; a posição antiga fechou com alteração explícita de fonte, entrada preservada e crédito único. O restante deste documento registra o aceite histórico da 5.3.4.1.

**Concluída e implantada — CP-PAPER-TRACKING-01, 10/09/2026.** Esta entrega fecha a ligação de coleta. A **5.3.4 permanece em andamento**: o paper ainda precisa consumir o preço de token_info com uma política explícita, na 5.3.4.2.

## O que mudou

A rota autenticada `GET /v1/paper/tracking` deriva a demanda das posições `entry_pending` e `open` já persistidas. Agrupa até quatro posições por identidade de origem/rede/token; dois lotes do mesmo token compartilham uma consulta. A regra pausada, a ausência no ranking ou a janela de features expirada não apagam essa demanda. O término da posição a retira do próximo snapshot.

O coletor sincroniza esse snapshot completo a cada 30 segundos, com no máximo uma nova consulta local de identidade por sincronização. O registro PostgreSQL continua obrigatório. Apenas o namespace do coletor, GMGN/Solana neste runtime, é encaminhado à fonte; outros aparecem na contagem de não atendidos. A demanda ganha uma concessão de 90 segundos no mesmo SQLite da fila. API indisponível ou contrato inválido não renovam essa concessão. A coleta já admitida conserva o prazo normal restante, sem renovação indefinida pela indisponibilidade.

Nenhum novo serviço, dependência, conexão permanente, migration PostgreSQL ou ledger financeiro foi criado. A pequena tabela `paper_tracking_demand` pertence ao SQLite existente. A posição persistida é a origem da demanda; a tabela é sua concessão local recuperável.

## Prioridade e limites

- Mantidos 10 tokens ativos, 20 em espera e uma chamada de token_info por 60 segundos.
- A frequência de cada token continua em 600 segundos. Posição aberta não implica atualização instantânea.
- Paper recebe prioridade de admissão. Uma vaga de descoberta cuja consulta não começou pode voltar à espera; fila, tentativas, evidências e próxima consulta são conservadas. Consultas em execução nunca são interrompidas.
- Se a espera já está cheia, um candidato de descoberta ainda na espera pode ser substituído pela demanda paper. Se não há vaga substituível, o estado continua `capacity_deferred`, sem ultrapassar o limite.
- A prioridade entre consultas vencidas continua limitada, com envelhecimento; a nova demanda não reseta cooldown, orçamento global ou frequência.
- O perfil admite no máximo quatro posições. Com os dez lugares atuais, descoberta continua tendo capacidade. Perfis menores não têm a mesma garantia de concorrência.
- A sincronização, a concessão e a admissão local são transacionais. Reinício não recria o job nem força consulta antecipada.
- `/v1/status → collector.tracking.paper` expõe posições vinculadas, estado, concessão, último recebimento/aceite e falhas de sincronização.
- Para um job paper novo, o campo legado `discovery_capture_id` usa a captura do registro como âncora de admissão. Uma sincronização paper não sobrescreve a descoberta de um job já existente.

No primeiro ensaio real, aguardar o vencimento normal dos dez jobs de descoberta deixaria a posição cerca de 16 minutos na espera. A passagem da vaga ainda não iniciada elimina esse atraso de admissão, sem aumentar chamadas. Erros de fonte, fila sem vaga segura e quota ainda podem atrasar a amostra.

## Validação

**308 testes passaram, sem falhas ou skips:** 256 da API na imagem candidata e 52 do coletor com rede desabilitada, incluindo 17 casos novos. Os casos de PostgreSQL usam schemas privados; os dados de preço dos testes são sintéticos.

Foram verificados: deduplicação por token, demanda após pausa/remoção da janela, 20 leitores sem novas trajetórias, autenticação, indisponibilidade do dono, término dos lotes, retomada, concessão vencida, identidade ausente, resposta atrasada, namespace incompatível, fila cheia, rollback SQLite, trabalho em execução, cooldown e frequência. Um job devolvido à espera conserva seu prazo quando retorna.

## Prova real read-only

Aceite em **11/09/2026 00:16:12Z**, horário UTC. A posição existente `f070bf0f79d5f709d9ded6fdc0828633dae24377db3465a1631350432a22a3e0`, do token `42H7fBC2eNvjcRftqmVraxbPmiLzu3KnTpd5eRnspump`, gerou a demanda sem POST de posição, regra ou vínculo.

- Token ausente nas três capturas mais recentes do ranking, com coleta do ranking atualizada.
- token_info real recebido às **00:14:02.198782Z**, interpretação disponível às **00:14:02.910189Z**, sem issues.
- Captura `8e8721c3-9ae2-4f96-98a0-5a778f909351`; preço bruto `0.0000028488537` USD/token.
- SHA256 do bruto conferido: `267c33527928e99f654da7bce5824d88a2ae7e0c2e54cbd34090514c71a05597`.
- API e coletor reiniciados depois do recebimento. A demanda voltou; job permaneceu com uma tentativa, mesmo UUID/aceite e próximo prazo. Nenhum POST adicional.
- Entrada, cotação de entrada, definição/política, três posições anteriores, conta e eventos da conta preservados.
- Conta permanece na revisão 6, caixa `72.953603070094381618002735186682117466370276` USD e custo investido `19.999999999999999999999999999999999115163664` USD, exclusivamente simulados.
- Todas as regras e vínculos continuam pausados. A demanda de coleta da posição aberta continua ativa.

**Não houve saída paper nesta entrega.** A posição permanece `open / window_unavailable`: sua janela original de ranking expirou às 00:12:14Z. Receber token_info não autoriza reinterpretar silenciosamente a política anterior nem creditar a conta.

## Runtime e evidências

API: `midas-data-app:paper-tracking-v1-20260910`, SHA `56961f5c2cbdbe976fa429fd5a72b98dddbc04a1a7ec47ce95b5770fe800ca05`.

Coletor: `midas-data-collector:paper-tracking-v2-20260910`, SHA `9a8e36963ab03008279bb48ca5ddbd0bee5ced834909bd149c2204ef205073a6`.

Quatro arquivos implantados conferidos contra o workspace. Worker, PostgreSQL, NATS e ClickHouse conservam IDs/imagens; seis serviços saudáveis. Tags salvas no env local do runtime. Backup consistente do SQLite conferido com integrity_check, além dos backups de arquivos do escopo.

Evidências em `.scratch/checkpoint-paper-tracking-20260910/`: `acceptance.json`, `test-results.json`, XMLs, `before.json`, `first-quote.json`, `after-restart.json`, bruto, snapshots do job antes/depois do reinício, hashes implantados e runtime. Sem ordem real, assinatura, broadcast ou mensagem externa.

## Próxima parte: 5.3.4.2

Conectar o consumo do preço token_info ao paper, independentemente da janela de ranking, com fonte/qualidade/frescor versionados. Para a posição existente, registrar explicitamente a alteração de fonte preservando entrada, definição original e trilha de evidência. Depois provar uma saída simulada com cotação futura elegível, crédito único e retomada. Ajustar a compatibilidade do prazo de entrada/frescor com a cadência de 600 segundos, sem prometer entrada ou saída instantânea.
