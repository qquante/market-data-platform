# 5.3.3 — regras vinculadas à conta paper

> Continuidade: [5.3.4.1 concluída](PAPER_TRACKING.md). A posição já recebeu token_info fora do ranking; próximo 5.3.4.2, consumo com fonte/política explícita. O restante deste documento registra o aceite histórico da 5.3.3.

**Vínculo e admissão automática concluídos e implantados — CP-PAPER-BINDINGS-01, 10/09/2026.** A **5.3 continua em andamento**: a prova real abriu a posição, mas o token saiu do ranking e não houve cotação elegível de saída. Próximo: **5.3.4**, acompanhamento de preço para posições abertas fora do ranking, usando a fila por token existente.

## Entrega

[paper_bindings.py](midas_data/paper_bindings.py) conecta uma regra escolhida à política paper e à conta `main`. A [migration 014](midas_data/migrations/014_paper_bindings.sql) guarda vínculo, cursor e eventos, além de um índice parcial para sinais. [paper_positions.py](midas_data/paper_positions.py) expõe internamente a mesma admissão usada pela API; ela pode participar da transação do vínculo.

O dono de janelas continua sendo único. Em cada passagem, as regras persistem avaliações, as posições existentes progridem e os vínculos tentam admitir novos sinais. Saídas/timeouts podem liberar saldo antes das novas admissões. Não há novo processo, conexão permanente, fila externa, biblioteca ou assinatura do coletor.

Há até **16 vínculos persistidos**, incluindo os pausados, um por regra. Conta e limites continuam os da 5.3.2; a execução de preço e saída continua a da 5.3.1.

## Ativação e pausa

O vínculo exige conta inicializada, definição de regra existente e política paper explícita. Pode ser preparado enquanto a regra está pausada. O orçamento configurado não pode exceder o limite por posição da conta.

Ao ativar ou retomar o vínculo, o cursor começa na sequência atual da regra. Somente sinais com sequência **posterior** podem gerar admissão. Sinais anteriores à primeira adesão ou emitidos durante a pausa não são reproduzidos. O evento de ativação registra a fronteira de sequência usada.

Repetir o cadastro de um vínculo já ativo e idêntico é idempotente: não move o cursor para frente nem descarta sinais pendentes. Outra política para a mesma regra responde 409; a definição deste vínculo é imutável no recorte.

Pausar o vínculo avança o cursor até a sequência atual e interrompe novas admissões. Entradas pendentes e posições já abertas continuam seguindo timeout/saída. O evento de pausa registra o intervalo descartado.

A regra de origem e o vínculo têm controles distintos: pausar a regra interrompe novas emissões, mas não retira sinais já persistidos. **Para interromper admissões, pause o vínculo.** O vínculo não habilita regras, não registra janelas e não renova suas adesões.

## Sinais, recusas e retomada

O histórico persistido de avaliações é a origem dos sinais. O vínculo busca `disposition='signal'` após seu cursor; não depende apenas da última decisão em memória, que pode ter mudado para supressão ou pausa.

Cada vínculo trata no máximo um sinal por passagem, em ordem crescente de sequência. Vínculos são percorridos por `rule_id` crescente. Com saldo disputado, essa ordem determina a precedência; não há promessa de prioridade comercial ou distribuição justa entre estratégias.

| Resultado | Efeito |
|---|---|
| `admitted` | Cria uma posição e reserva saldo uma única vez |
| `existing` | O mesmo sinal já tinha posição com a mesma política; não cobra novamente |
| `rejected` | Registra a recusa sem criar posição ou reserva |

Saldo insuficiente, limites, sinal velho/futuro, janela não registrada e conflito com consumo manual geram recusa com motivo. A recusa é **final para a tentativa automática desse sinal**. Liberar caixa depois não causa retry automático; uma nova emissão é necessária. A API manual existente continua disponível para solicitações explícitas e mantém a unicidade por sinal.

Falhas de infraestrutura não viram recusas de negócio: interrompem a passagem, fazem o dono recarregar os registros duráveis e permitem nova tentativa. Se o sinal ficou velho durante a indisponibilidade, a retomada registra a recusa por idade.

## Atomicidade e custo do loop

Admissão, posição, reserva/conta, evento do vínculo e avanço do cursor participam da **mesma transação PostgreSQL**. As transações internas existentes atuam como savepoints. Assim, um rollback do vínculo desfaz também a reserva e a posição; um commit confirmado no banco, mesmo com confirmação perdida para o processo, é recuperado sem duplicação.

Há chave única por regra/sequência no histórico de tentativas. O evento admitido aponta para a posição; a posição aponta para o sinal; seus ledgers apontam para conta e frames. A definição versionada inclui política paper, identidade da conta, fronteira de ativação, semântica de recusa e ordem de processamento.

O loop inspeciona até 16 cursores em memória. Um vínculo sem mudança na sequência da origem não consulta SQL. Quando há mudança, faz uma busca indexada e limitada a um sinal; se não houver sinal, avança diretamente até a sequência observada. Avanços sem sinal não acrescentam eventos ao histórico. Leitores de status usam memória e não consultam trajetórias.

## API

Todas as rotas usam a autenticação existente:

- `POST /v1/paper/bindings`: ativa ou retoma; corpo até 8 KiB, retorno 202.
- `GET /v1/paper/bindings`: até 16 vínculos, estado, cursor, revisão, identidade da definição e último evento.
- `POST /v1/paper/bindings/{rule_id}/pause`: pausa idempotente, retorno 204.
- `GET /v1/paper/bindings/{rule_id}/events`: até 100 eventos, cursor `before_revision`.

Exemplo técnico de cadastro:

```json
{
  "rule_id": "<id da definição existente>",
  "policy": {
    "budget_usd": "20",
    "fee_bps": 100,
    "slippage_bps": 100,
    "entry_latency_seconds": 0,
    "entry_timeout_seconds": 180,
    "hold_seconds": 1,
    "max_price_age_seconds": 120,
    "max_signal_age_seconds": 120
  }
}
```

Versão padrão: `gmgn-paper-binding-v1`. Valores acima são um cenário técnico simulado, sem configuração ou execução de estratégia financeira real.

## Validação

**254 testes passaram**, zero falhas/erros/skips, em **33,997 s**, na imagem candidata Python 3.13: 11 novos casos e 243 regressões. [Testes dos vínculos](tests/test_paper_bindings.py) cobrem:

- Cadastro concorrente, idempotência, sinais anteriores excluídos e conflito de política.
- Admissão automática, pausa com posição aberta e saída independente.
- Recusa única por falta de saldo, sem retry quando o saldo volta.
- Pausa/retomada descartando backlog e retomada persistente.
- Consumo manual concorrente, com política igual ou diferente.
- Sinal atrasado recusado sem reserva.
- Rollback depois de gravar posição/conta, confirmação perdida do commit externo e recuperação quando a última avaliação já foi sobrescrita.
- Ausência de SQL do vínculo quando a sequência não muda.

Integrações usam PostgreSQL real com preços sintéticos em schemas privados. Um double do teste inicialmente apontou para uma conexão já devolvida ao pool; foi corrigido, seu schema residual foi removido após conferir nome/data e a regressão final passou. [Evidência da limpeza](../.scratch/checkpoint-paper-bindings-20260910/failed-fixture-cleanup.json).

**Prova com dados reais, execução exclusivamente simulada:** um vínculo para o token `42H7fBC2eNvjcRftqmVraxbPmiLzu3KnTpd5eRnspump`, presente em 10/10 capturas recentes, foi restaurado após reinício sem novo POST de cadastro. Ele admitiu automaticamente o sinal **6** e criou a posição `f070bf0f79d5f709d9ded6fdc0828633dae24377db3465a1631350432a22a3e0`. O probe não fez POST manual de posição. Vinte leituras de status não acrescentaram consultas de trajetória.

A política técnica usa orçamento de 20 USD, fee/slippage de 100 bps por lado, latência zero, timeout de entrada de 180 s e hold de 1 s. A posição abriu às **23:44:52Z**, com preço de referência `0.00000444626` USD/token. O vínculo e a regra foram pausados após a admissão; a posição continua sendo tratada pelo motor.

**Saída real desta rodada não comprovada:** o token deixou de aparecer nas capturas de ranking posteriores a **23:44:50Z**. A consulta às últimas oito capturas mostrou o ranking atualizado até 23:47:50Z, mas sem nova amostra do token após a entrada. O probe encerrou sua espera de 260 s por timeout; isso não foi um fechamento da posição. [Diagnóstico da cobertura](../.scratch/checkpoint-paper-bindings-20260910/coverage-diagnostic.json).

No aceite às **23:50:18Z**, a posição permanecia aberta com `awaiting_new_price`. A conta avançou da revisão 4 para 6, com uma reserva e uma entrada, sem crédito de saída. Caixa simulado: `72.953603070094381618002735186682117466370276` USD; custo investido: `19.999999999999999999999999999999999115163664` USD; reserva zerada e P&L realizado anterior inalterado. Não foi imputado preço zero, venda ou perda realizada à ausência de cotação.

O frame e preço de entrada foram conferidos pelo SHA e valuation de origem. Três posições antigas, seus eventos, o ledger anterior da conta e três respostas históricas permaneceram iguais. Há **uma posição simulada aberta, zero regras habilitadas e zero vínculos habilitados**. A janela foi conservada até **00:12:14Z de 11/09/2026**, sem renovação automática; uma nova amostra elegível nesse período ainda pode encerrar a posição. Se a adesão expirar, a posição permanece aberta e o custo continua comprometido. Todos os horários são UTC.

Runtime: `midas-data-app:paper-bindings-v1-20260910`, SHA `c7dce759d498743e88b9f944f260eafa4074fd2211e62c05922af21b201ab196`. Somente a API mudou; os cinco outros containers mantiveram IDs e imagens. Os seis estavam saudáveis às **23:51:02Z**. Os quatro arquivos de aplicação/migração alterados foram comparados por SHA com o workspace.

Evidências: [checkpoint](../.scratch/checkpoint-paper-bindings-20260910/), [testes finais](../.scratch/checkpoint-paper-bindings-20260910/contract-tests.xml), [vínculo antes do reinício](../.scratch/checkpoint-paper-bindings-20260910/before-restart.json), [vínculo recuperado](../.scratch/checkpoint-paper-bindings-20260910/resumed-binding.json), [admissão automática](../.scratch/checkpoint-paper-bindings-20260910/automatic-admission.json), [posição e ledgers finais](../.scratch/checkpoint-paper-bindings-20260910/after-restart.json), [aceite real](../.scratch/checkpoint-paper-bindings-20260910/live-acceptance.json) e [runtime](../.scratch/checkpoint-paper-bindings-20260910/runtime-acceptance.json).

## Limite do aceite e próximo passo

O aceite da 5.3.3 cobre vínculo, admissão automática, recuperação e entrada real simulada. O fechamento com regra/vínculo pausado passou em integração com preços sintéticos; o fechamento do canário live permanece pendente por cobertura da origem. Isso impede declarar a 5.3 encerrada nesta rodada.

A **5.3.4** deve resolver primeiro a base operacional: ligar posições abertas ao acompanhamento por token já existente na 3.4, com fonte/política de preço explícitas, respeito à quota e à idade da cotação, e retomada de seus interesses. A execução não deve depender de o token continuar no ranking que gerou o sinal. A posição atual e sua evidência devem ser conservadas; não se deve substituir silenciosamente a fonte/política histórica para forçar seu fechamento.

TP/SL, saída parcial, rebalanceamento e mark-to-market permanecem fora deste recorte. Replay equivalente e cenários maiores continuam na 5.4, depois dessa correção de cobertura. Não há necessidade de medição extensa para tratar esta lacuna observada.
