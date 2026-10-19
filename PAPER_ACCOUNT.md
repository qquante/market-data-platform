# 5.3.2 — saldo paper compartilhado e limites de exposição

**Concluída e implantada em 10/09/2026 — CP-PAPER-ACCOUNT-01.** Este documento registra o checkpoint 5.3.2. A continuação [5.3.3 — vínculos automáticos](PAPER_BINDINGS.md) já foi implantada; consulte-a para o saldo/estado posterior e a posição que aguarda preço fora do ranking. A 5.3 continua em andamento.

## Escopo entregue

Uma conta simulada `main` por schema, criada explicitamente com saldo inicial e limites. Novas posições exigem essa conta. A inicialização repetida com a mesma configuração retorna a conta existente, sem depositar novamente; uma configuração diferente responde 409. Não há reset, depósitos posteriores ou alteração automática de limites neste recorte.

As posições anteriores à migration 013 mantêm `account_id = null`, política e ledger originais. Continuam recuperáveis e consultáveis como lotes isolados e não alteram o saldo novo. O identificador por sinal continua único entre os dois modelos; reenviar uma posição antiga não cria outra nem cobra a conta.

- [paper_account.py](midas_data/paper_account.py): configuração versionada, contabilidade, limites e persistência.
- [013_paper_account.sql](midas_data/migrations/013_paper_account.sql): conta, ledger da conta e vínculo opcional nas posições.
- [paper_positions.py](midas_data/paper_positions.py): reserva, débito, liberação e crédito dentro da transação da posição.
- [api.py](midas_data/api.py): inicialização, saldo e histórico.

Reutiliza o dono, conexão, loop e lock existentes. Não adiciona serviço, biblioteca, assinatura de coleta ou consulta de trajetória por leitor.

## Semântica do saldo

Todos os valores são strings decimais em USD simulado. `cash_usd` inclui o dinheiro reservado, ainda não debitado.

| Campo | Significado |
|---|---|
| `cash_usd` | Dinheiro em caixa; diminui na entrada e aumenta na saída |
| `reserved_usd` | Orçamentos das entradas ainda pendentes |
| `available_usd` | Caixa menos reserva; pode financiar novas entradas |
| `invested_cost_usd` | Custo já debitado das posições abertas, incluindo fee |
| `committed_usd` | Reserva mais custo das posições abertas |
| `realized_pnl_usd` | Resultado líquido das posições fechadas da conta |
| `active_positions` | Entradas pendentes mais posições abertas |

A exposição é medida por **orçamento reservado e custo de entrada**, não por valor de mercado corrente. Não é mark-to-market ou estimativa de risco de liquidação.

| Evento | Efeito na conta |
|---|---|
| Solicitação aceita | Reserva o orçamento completo e ocupa uma vaga |
| Entrada | Debita o custo efetivo, remove a reserva e registra o custo investido |
| Entrada expirada | Libera toda a reserva e a vaga, sem débito |
| Saída | Credita os proceeds líquidos, remove o custo investido e libera a vaga |
| Espera por preço | Não altera o saldo e não gera evento na conta |

O resíduo do orçamento não usado fica disponível assim que a entrada é contabilizada. A saída credita apenas os proceeds: o resíduo não é creditado uma segunda vez. Lucros e perdas permanecem no caixa; a inicialização repetida não recompõe perdas.

Invariantes: `cash >= reserved >= 0`, `invested_cost >= 0` e `cash + invested_cost = initial_cash + realized_pnl`. Aritmética usa o mesmo contexto decimal explícito da 5.3.1, sem arredondar o saldo a centavos após cada evento. A configuração inicial aceita centavos; a simulação fracionária pode produzir resíduos menores.

## Limites e consistência

Toda nova admissão verifica:

1. Orçamento até `max_position_usd`.
2. Vaga dentro de `max_open_positions`, entre 1 e 4.
3. Saldo disponível suficiente para o orçamento completo.
4. Reserva + custo investido + novo orçamento até `max_exposure_usd`.

Uma recusa retorna 429 com motivo e desfaz a transação: não consome o sinal, não reserva saldo e não deixa posição/evento órfão. Sinais ainda frescos podem ser tentados novamente quando houver espaço. A repetição de uma admissão aceita retorna a posição existente, sem nova reserva.

Estado da posição, evento da posição, frame, saldo e evento da conta ficam na **mesma transação**. A linha da conta é lida com `FOR UPDATE`; a gravação também verifica a revisão esperada. O ledger da conta aponta para a revisão correspondente da posição e impede repetir esse vínculo. A política contábil e os limites participam da identidade da definição das posições novas. Sem mudança na política de preços e saída da 5.3.1.

No reinício, o dono recarrega até quatro posições ativas e compara suas reservas/custos com os totais da conta. Divergência suspende o dono em vez de expor saldo utilizável. O saldo é lido da linha persistida; não existe um segundo cache monetário para reconciliar.

Fundamentação técnica: [locks de linha do PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS) e [contexto decimal local do Python 3.13](https://docs.python.org/3.13/library/decimal.html#decimal.localcontext). Os testes abaixo verificam a aplicação desses mecanismos neste projeto.

## API e configuração técnica

Rotas autenticadas pelo Bearer existente:

- `POST /v1/paper/account`: cria uma única vez; retorna 202 com `created`.
- `GET /v1/paper/account`: último saldo persistido, configuração, definição, revisão e timestamps; 404 antes da inicialização.
- `GET /v1/paper/account/events`: até 100 eventos por página, cursor `before_revision`.
- `POST /v1/paper/positions`: formato da 5.3.1 preservado; novas admissões exigem conta inicializada, senão 409.

Inicialização usada **somente como cenário técnico do canário**:

```json
{
  "initial_cash_usd": "100",
  "max_position_usd": "60",
  "max_exposure_usd": "100",
  "max_open_positions": 2
}
```

Versão padrão `gmgn-paper-account-v1`. Valores e quantidade são explícitos, sem configuração automática de estratégia. Os limites monetários aceitam valores positivos até 999.999.999,99 USD; limite por posição não pode exceder o total de exposição. Nenhuma chave, carteira, ordem ou transação real participa deste fluxo.

## Validação

**243 testes passaram**, zero falhas/erros/skips, em **26,340 s**, na imagem candidata Python 3.13. São 24 casos novos e 219 regressões, com asserções monetárias adicionais no teste de posições.

- [Modelo da conta](tests/test_paper_account.py): custos conhecidos, resíduo fracionário, perda que reduz o saldo futuro, timeout e os quatro limites de admissão.
- [Integração da conta](tests/test_paper_account_integration.py): PostgreSQL em schema privado, 20 inicializações concorrentes sem crédito duplicado, dois sinais disputando saldo, falta de caixa/exposição/vagas, rollback após gravações, confirmação perdida após commit externo, reinício e liberação por timeout; também migração de lote isolado pendente e retomada sem afetar a conta.
- [Integração de posições](tests/test_paper_positions_integration.py): caixa e ledger conferidos na entrada/saída, inclusive perda da confirmação dos commits, regra pausada e quatro expirações com liberação das reservas.

**Prova com dados reais e execução exclusivamente simulada:** token `9Gvaz8yoNwyqFFWU2mF1o27YdYDLW16bT6QfkQrFpump`, presente em 10/10 capturas recentes do ranking. Duas solicitações de 60 USD disputaram 100 USD: uma foi aceita, deixando 60 reservados e 40 disponíveis; a outra recebeu 429 sem consumir o sinal. Vinte leitores do saldo acrescentaram zero consultas de trajetória.

A API foi reiniciada com a entrada pendente e a reserva foi conferida após a retomada, antes da entrada. Repetir inicialização/admissão não creditou nem reservou novamente. A política técnica usou fee e slippage de 100 bps por lado, latência de entrada de 60 s, timeout de 180 s e hold de 1 s. A posição abriu às **23:06:46Z** e fechou às **23:07:48Z**, aguardando uma nova amostra elegível.

Preços de referência: `0.000011346` e `0.0000104222` USD/token. Saldo final **simulado**: `92.95360307009438161800273518668211658153394` USD; resultado líquido: `-7.04639692990561838199726481331788341846606` USD. O ledger da conta terminou na revisão 4, com inicialização, reserva, entrada e saída, uma ocorrência de cada. Reserva e custo investido terminaram zerados.

Os dois frames/preços foram conferidos nas valuations de origem; duas posições históricas e seus eventos, além de três consultas históricas, permaneceram iguais. Ao encerrar: **zero posições abertas, zero regras habilitadas**, janela técnica removida e ledgers preservados. Aceite às **23:07:49Z**, em 10/09/2026; todos os horários em UTC. A conta permanece com o saldo técnico final e limites configurados, sem estratégias ativadas.

Runtime: `midas-data-app:paper-account-v1-20260910`, SHA `972595991b6943977485f2516459864841b934018c6c576c81df3fdb684c4bd8`. Somente a API mudou; os cinco outros containers mantiveram IDs e imagens. Os seis estavam saudáveis às **23:08:53Z**. Os quatro arquivos de aplicação/migração alterados foram comparados por SHA com o workspace e estavam iguais.

Evidências: [checkpoint](../.scratch/checkpoint-paper-account-20260910/), [testes](../.scratch/checkpoint-paper-account-20260910/contract-tests.xml), [antes do reinício](../.scratch/checkpoint-paper-account-20260910/before-restart.json), [reserva recuperada](../.scratch/checkpoint-paper-account-20260910/resumed-initial.json), [estado e ledgers finais](../.scratch/checkpoint-paper-account-20260910/after-restart.json), [aceite real](../.scratch/checkpoint-paper-account-20260910/live-acceptance.json) e [runtime](../.scratch/checkpoint-paper-account-20260910/runtime-acceptance.json).

## Próximo recorte

A [5.3.3 — adesão de regras selecionadas à conta paper](PAPER_BINDINGS.md) foi implantada. A admissão manual continua disponível; vínculos explícitos podem admitir sinais futuros automaticamente. A prova live revelou a necessidade da **5.3.4**, acompanhamento de posições abertas quando o token sai do ranking. Replay equivalente e carga de estratégias permanecem na 5.4.

Continuam os limites da 5.3.1: preço amostrado, fill fracionário hipotético, ausência de liquidez executável e saída pendente quando falta preço novo. A adesão das janelas é de 30 minutos e precisa ser renovada pelo consumidor; uma posição aberta conserva seu custo comprometido se a janela expirar.
