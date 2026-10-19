# 5.3.1 — primeira posição e ledger paper

> Registro do checkpoint 5.3.1. A [5.3.2 já está concluída](PAPER_ACCOUNT.md): novas posições exigem conta paper compartilhada; os lotes antigos descritos abaixo mantêm orçamento e ledger isolados.

**Concluída e implantada em 10/09/2026**, CP-PAPER-01, A0, sem subagentes. A **5.3 permanece em andamento**. Este recorte consome explicitamente um sinal, abre um lote simulado e o fecha por tempo. Continuação entregue: [5.3.2 — caixa paper compartilhado e limites de exposição](PAPER_ACCOUNT.md).

## Entrega mínima

- [paper_model.py](midas_data/paper_model.py): política versionada, elegibilidade de preço e aritmética.
- [paper_positions.py](midas_data/paper_positions.py): consumo único, estado persistente e progressão automática.
- [012_paper_positions.sql](midas_data/migrations/012_paper_positions.sql): posições e eventos do ledger, com referência ao sinal e aos frames imutáveis.
- [api.py](midas_data/api.py) e [feature_windows.py](midas_data/feature_windows.py): integração com o dono, conexão e loop existentes.

A admissão é explícita por API; não transforma automaticamente todos os sinais em posições. Depois de admitida, a posição progride no loop existente, mesmo se a regra de entrada estiver pausada. Há até **quatro posições pendentes/abertas**, cada uma com orçamento isolado. As posições concluídas permanecem consultáveis e deixam o conjunto ativo em memória.

Não há nova biblioteca, serviço, conexão permanente, coleta adicional ou integração com os executores legados. O modelo não usa market cap como preço.

## Preço e tempo

O sinal precisa existir no ledger interno com disposição `signal`, estar dentro da idade máxima configurada e não estar no futuro. A janela correspondente precisa estar registrada. O sinal só pode ser consumido uma vez: repetir a mesma política retorna a posição existente; tentar outra política para o mesmo sinal responde 409.

A entrada começa em `entry_pending`. Só pode usar uma **observação recebida após a solicitação e sua latência configurada**, com preço positivo, homologado e em `usd_per_token`, conhecido no corte do frame. Não usa o preço histórico do sinal como se uma compra tivesse ocorrido naquele instante. Também não revalida a condição da regra depois de consumir o sinal; essa semântica faz parte da política.

A idade do preço precisa respeitar tanto o limite paper quanto o gap da janela. Última observação inválida/ambígua, ausência de preço ou frame vencido mantêm a espera. A entrada expira no timeout configurado, sem quantidade comprada ou débito.

Após a entrada, o prazo de saída é contado a partir da abertura processada. No prazo, o motor precisa consumir uma nova observação recebida no prazo ou depois dele. Se o preço estiver ausente, a posição **continua aberta**, com motivo de espera; não há fechamento fictício. Se várias observações chegam entre avaliações, usa a cauda fresca do frame efetivamente consumido, sem reconstruir um fill anterior.

A adesão da janela continua limitada a 30 minutos e não é renovada pelo paper. Para acompanhar por mais tempo, o consumidor precisa renová-la; janela ausente/vencida não produz saída, e o motivo fica registrado. Essa limitação também vale quando `hold_seconds` excede a adesão restante.

Este é um modelo de preço amostrado, com fill fracionário integral hipotético. Não demonstra liquidez executável, ordem intrabar, gas, MEV, confirmação on-chain ou possibilidade de executar o tamanho simulado.

## Custos e caixa

O chamador informa orçamento, fee e slippage; os valores são cenários explícitos, sem inferir tarifas reais do provedor.

- Preço de compra: preço observado × (1 + slippage/10.000).
- Quantidade: orçamento ÷ (preço de compra × (1 + fee/10.000)), arredondada para baixo em 34 algarismos significativos.
- Débito: quantidade × preço de compra + fee; o resíduo não gasto é preservado.
- Preço de venda: preço observado × (1 − slippage/10.000).
- Crédito: quantidade × preço de venda − fee.
- Caixa final: resíduo + crédito. P&L simulado: caixa final − orçamento inicial.

A aritmética monetária usa contexto explícito de até 512 dígitos e falha se uma operação exigir arredondamento inesperado. Quantidade fracionária é uma convenção do modelo, sem ajuste por decimals on-chain. O orçamento aceita centavos positivos até 999.999.999,99 USD; fees/slippage aceitam 0 a 5.000 bps por lado. O preço tem orçamento numérico limitado e não recebe fallback de market cap.

Neste checkpoint ainda não havia caixa comum entre posições ou reserva compartilhada; ambos foram adicionados na [5.3.2](PAPER_ACCOUNT.md). Mark-to-market contínuo e limites por estratégia permanecem pendentes. O status informa o último estado persistido do lote, não uma cotação corrente.

## Estado, evidência e recuperação

`entry_pending → open → closed`; a admissão sem preço pode terminar em `entry_expired`.

O registro do sinal consumido e o primeiro evento são atômicos. Cada mudança posterior grava estado/revisão, evento e frame de evidência na mesma transação. A sequência esperada impede atualizar um estado antigo. Chaves únicas impedem consumir novamente o sinal ou duplicar eventos após perda de confirmação.

O ledger contém os débitos/créditos em strings decimais e aponta para o frame imutável compartilhado da 5.2.2. A cotação registra captura, item/pointer de preço, interpretação, raw SHA, anchor fact, corte e timestamps. O preço pode ser resolvido pela consulta de valuation do mesmo fato/corte.

No reinício, apenas as posições pendentes/abertas são recarregadas. Entradas já gravadas conservam quantidade, custo e prazo. Uma saída gravada não é executada novamente. Retomada não inventa fills em intervalos sem dados.

## API

Rotas autenticadas pelo Bearer existente:

- `POST /v1/paper/positions`: corpo até 8 KiB; consome um sinal, retorna 202 e `created`.
- `GET /v1/paper/positions`: até 100 por página, cursor `before_number`.
- `GET /v1/paper/positions/{position_id}`: política completa e último estado persistido.
- `GET /v1/paper/positions/{position_id}/events`: até 100 eventos por página, cursor `before_revision`.
- `GET /v1/rule-frames/{frame_key}`: evidência original compartilhada.

Formato do corpo:

```json
{
  "rule_id": "<id do cadastro ativo>",
  "signal_sequence": 1,
  "policy": {
    "budget_usd": "100",
    "fee_bps": 100,
    "slippage_bps": 100,
    "entry_latency_seconds": 0,
    "entry_timeout_seconds": 180,
    "hold_seconds": 60,
    "max_price_age_seconds": 120,
    "max_signal_age_seconds": 120
  }
}
```

A versão padrão é `gmgn-paper-timed-lot-v1`. A identidade da política inclui esses parâmetros, versões de valuation/trajetória, arredondamento, modelo de fill e regras de entrada/saída. Os valores do exemplo são ilustrativos.

## Validação

**219 testes passaram**, zero falhas, erros ou skips, em **22,639 s**, na imagem candidata Python 3.13. São 34 novos casos e 185 regressões.

[Testes do modelo](tests/test_paper_model.py): aritmética independente do contexto ambiente, resíduo de caixa, fees/slippage, preço ausente/zero/inválido/ambíguo, cortes, futuro, frescor, entrada posterior à solicitação, timeout e saída com preço novo.

[Integração PostgreSQL](tests/test_paper_positions_integration.py): schema privado e dados sintéticos; 20 admissões concorrentes criam um lote; conflito de política; sinais velhos/futuros recusados; falha antes de gravar a entrada; confirmação perdida após commit de entrada e de saída; recuperação no loop real; fechamento com regra pausada; limites e paginação. O preço varia de 2 para 3 e o market cap de 200 para 600 por mudança de supply: o lucro paper usa a variação do preço, não a do cap.

**Prova no runtime com dados reais, execução exclusivamente simulada:** o primeiro token (`4rjDqoTz…pump`) deixou de aparecer no ranking; a entrada expirou após 180 s sem débito. As capturas/publicações continuaram atualizadas. O caso permanece no ledger e em [first-attempt.json](../.scratch/checkpoint-paper-20260910/first-attempt.json), sem fabricar compra usando preço antigo.

O segundo token (`8csyWk4J…pump`), selecionado por presença em 10/10 capturas recentes, abriu às **22:40:44Z** e fechou às **22:41:43Z**. A API foi reiniciada enquanto a posição estava aberta. O resultado conservou a entrada e registrou **uma entrada, uma saída e zero consumo duplicado**. A política técnica usou orçamento de 100 USD, fee e slippage de 100 bps por lado, hold de 1 s e timeout de entrada de 180 s. O fechamento aguardou a próxima cotação elegível; hold de 1 s não significa preço instantâneo nem fill em 1 s.

Preços de referência: `0.0000965216` na entrada e `0.0000924492` na saída. O P&L **simulado** foi `-7.97490242726313325737856239407229651437660` USD após os custos configurados, sem movimentar dinheiro. Os dois frames foram conferidos pelo SHA e reconstruídos no mesmo corte; os dois preços foram resolvidos pela valuation do fato de origem. Três consultas históricas permaneceram iguais e 20 leitores de status acrescentaram zero consultas de trajetória. Repetir a admissão após o fechamento retornou a posição existente.

Ao concluir o canário, havia **zero posições abertas e zero regras habilitadas**. Os dois ledgers foram preservados. A primeira janela técnica foi removida; a segunda tem adesão até **23:10:15Z de 10/09/2026**, sem renovação automática. Aceite funcional às 22:45:42Z; conferência do runtime às 22:47:26Z. Todos os horários são UTC.

Runtime: `midas-data-app:paper-v1-20260910`, SHA `f69f988b808373f890d0569ee2310b0d4afbfed9745917461641d28db7751c43`. Somente a API mudou; os cinco outros containers mantiveram IDs e imagens, e os seis estavam saudáveis no aceite. Os cinco arquivos de aplicação/migração alterados foram comparados por SHA com o workspace.

Evidências: [checkpoint-paper-20260910](../.scratch/checkpoint-paper-20260910/), [testes finais](../.scratch/checkpoint-paper-20260910/contract-tests.xml), [aceite real do modelo](../.scratch/checkpoint-paper-20260910/live-acceptance.json), [posição antes do reinício](../.scratch/checkpoint-paper-20260910/before-restart.json), [posição e ledger finais](../.scratch/checkpoint-paper-20260910/after-restart.json) e [runtime](../.scratch/checkpoint-paper-20260910/runtime-acceptance.json).

## Falta na 5.3

A continuação **5.3.2: caixa paper compartilhado e limites de exposição** foi [concluída](PAPER_ACCOUNT.md), impedindo que novas posições usem o mesmo saldo simulado simultaneamente. Depois entram adesão automática a sinais, políticas adicionais de saída e tratamento mais amplo de posições. Replay equivalente e benchmarks de estratégias continuam em 5.4; limpeza/retenção de histórico e carga ampliada não fazem parte deste aceite.
