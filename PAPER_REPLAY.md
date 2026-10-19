# Replay mínimo do paper — CP-PAPER-REPLAY-01

> Continuação concluída: [5.4.2 — replay de dois lotes](PAPER_REPLAY_PAIR.md), 355 testes e conta reconciliada por revisão global. Próximo 5.4.3. O registro abaixo descreve o checkpoint anterior de um lote.

Etapa **5.4.1 concluída em 10/09/2026** (aceite em 11/09/2026 às 01:22:07Z). A etapa 5.4 continua em andamento; próximo recorte **5.4.2**, duas posições com movimentos intercalados na mesma conta.

## Entrega

`midas_data.paper_replay` exporta um lote terminal da conta compartilhada em transação PostgreSQL `REPEATABLE READ, READ ONLY` e reproduz seus registros offline. Não abre `Store`, não aplica migrations e não chama o coletor ou provedor.

O replay reutiliza `rules.evaluate`, `rule_timers.advance`, `paper_model.transition` e `paper_account.account_change`. A comparação aceita um relógio explícito opcional; o comportamento padrão online foi preservado. O estado inicial do lote foi extraído para `requested_state`, compartilhado entre o criador online e o replay.

A reprodução verifica:

- Definições versionadas, IDs, sequências completas e SHA256 dos bytes de cada frame.
- Condição, debounce, continuidade, elegibilidade e emissão até o sinal que originou a posição.
- Entrada, esperas, alteração explícita de fonte, saída/timeout, cotações, proveniência, taxas e slippage.
- Horário UTC de cada ação, publicação e disponibilidade das observações; dados posteriores ao corte são recusados.
- Reserva, débito, crédito e estado final da posição/conta, com a mesma aritmética decimal.

Dados de avaliação, preenchimento e saldo são recalculados e comparados campo a campo. O relatório retorna `verified` ou `failed`; uma divergência identifica seu caminho. Um histórico incompleto não recebe aprovação.

## Prova real

Cotações reais e execução financeira somente simulada. Posição `f070bf0f79d5f709d9ded6fdc0828633dae24377db3465a1631350432a22a3e0`, token `42H7fBC2eNvjcRftqmVraxbPmiLzu3KnTpd5eRnspump`.

| Comparação | Resultado |
|---|---|
| Avaliações da regra até o sinal 6 | 6 reproduzidas, incluindo debounce e retomada |
| Histórico completo da posição | 42 eventos reproduzidos |
| Movimentos da conta | 3, revisões 5 a 7 |
| Frames imutáveis | 39, total de 322.190 bytes |
| Divergências | Zero |
| Saída simulada recalculada | 11.48639523559742990366339477369862363522789816 USD |
| P&L do lote | -8.51360476440257009633660522630137547993576584 USD |
| Saldo final da conta | 84.43999830569181152166612996038074110159817416 USD |

A entrada por ranking foi mantida. A mudança de fonte da revisão 37 e a saída por token_info da revisão 42 foram reproduzidas com os horários originais. O replay offline, executado em Docker com `--network none` e sem credenciais, gerou relatório integralmente idêntico.

SHA256 canônico do bundle: `96561a417f554425815747023c7f1baa6751d5abe134b9d7979fd5884443bfb9`.

## Validação e runtime

**330 testes passaram, zero falhas/erros/skips, em 42,885 s** na imagem final: 270 regressões e 60 casos de replay. SQL real em schemas privados e preços sintéticos; ciclos v2, legado com alteração de fonte e timeout. O caso de timeout usa o prazo explicitamente, sem esperar o relógio real. Os testes conferem retomada, ausência de dependência do relógio atual e detecção de alterações em decisões, preços, saldo, identidade, tempo, frames e revisões. Uma tentativa de escrita dentro da transação do exportador foi efetivamente recusada pelo PostgreSQL no schema de teste.

Imagem auxiliar construída e executada: `midas-data-app:paper-replay-v1-20260910`, SHA `c910e8dda55b237cce445fe1594e966ca59e713255f7c7a67e2e0b4842d8a51f`.

Os seis serviços existentes mantiveram IDs/imagens e estão saudáveis. A API continua em `paper-prices-v2-20260910`; esta entrega é uma ferramenta avulsa, sem implantação/reinício da API. Ledger, conta, quatro posições, histórico da posição selecionada e demandas foram comparados com o checkpoint anterior e permanecem idênticos. Zero posições abertas, regras/vínculos habilitados ou demandas paper. Nenhuma ampliação de quota, novo serviço, dependência ou migration.

## Limites deste recorte

O replay usa os **frames e horários de ações já registrados**. Disponibilidade do dono da janela, pausas, retomadas e pedido de alteração da fonte são entradas históricas. Ele não reconstrói ticks não registrados nem repete normalização de brutos.

O saldo anterior, revisão 4 da conta, é um **checkpoint confiado**. São recalculadas as revisões 5–7 deste lote; o histórico de financiamento e dos outros lotes não foi reexecutado. Movimentos intercalados de outra posição são recusados, sem ignorá-los. O hash confere integridade do bundle, não autenticação independente da origem.

Limites explícitos: uma posição terminal da conta main, até 512 avaliações anteriores ao sinal e 512 eventos da posição, 4 MiB por frame, 8 MiB de frames e 16 MiB por bundle. Frames incompatíveis não preservados pelo online impedem reprodução. A janela de admissão original já expirada não é recriada; o replay verifica o sinal e sua idade, não revalida a adesão histórica da janela.

## Reproduzir

Na raiz do workspace, usando o bundle já exportado:

```powershell
$evidence=(Resolve-Path .scratch/checkpoint-paper-replay-20260910).Path
docker run --rm --network none --entrypoint python -v "${evidence}:/evidence" midas-data-app:paper-replay-v1-20260910 -m midas_data.paper_replay --input /evidence/bundle.json --report /evidence/offline-replay.json
```

Para exportar novamente um lote existente, a CLI aceita `--position-id <id> --export <bundle> --report <relatorio>` em um processo com `DATA_DATABASE_DSN` já configurado. O modo `--input` dispensa banco e credenciais. Não registra regra, posição, vínculo ou ordem.

Evidências locais em `.scratch/checkpoint-paper-replay-20260910/`: `bundle.json`, `online-replay.json`, `offline-replay.json`, `acceptance.json`, `contract-tests.xml`, `test-results.json`, `runtime-verification.json`, build e backups anteriores.

Próximo **5.4.2**: estender somente a reconciliação da conta para dois lotes com reserva/entrada/saída intercaladas, preservando ordem e impedindo crédito duplicado. Cenários 20/100 e medições sustentadas continuam posteriores.
