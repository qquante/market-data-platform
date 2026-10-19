# Replay de recusas da conta — CP-PAPER-REPLAY-03

**5.4.3 concluída e implantada em 10/09/2026.** Os recortes funcionais 5.4.1–5.4.3 estão concluídos. A etapa 5.4/M3 permanece aberta para carga 20/100, deixada para a fase live conforme o escopo enxuto pedido.

Próximo trabalho: **6.1**, começar pelo contrato mínimo de holders e sua evidência disponível. A expansão documental de catálogo antes de M3 já é permitida pelo [plano de execução](../docs/gmgn_api/refactor/PLANO_EXECUCAO_E_CONTROLE.md); não implica ampliar coleta.

## O que mudou

A recusa automática antes guardava somente o motivo. Agora, quando o limite é decidido em `PaperAccount.apply`, o código conserva uma referência compacta à conta **enquanto sua linha ainda está bloqueada**, antes do rollback da tentativa:

- Versão `gmgn-paper-account-check-v1` e instante UTC usado na admissão.
- ID, revisão e política da conta.
- SHA256 canônico do estado observado.

O evento `rejected` do vínculo recebe essa referência na mesma transação do avanço de seu cursor. A tentativa recusada não cria posição nem reserva/débito. Se a gravação da recusa falhar, evento e cursor são revertidos; se o commit ocorrer e a confirmação se perder, a retomada conserva o evento único.

O verificador `paper_admission_replay.py` reaproveita o núcleo de regras/timers e `account_change`. Ele reproduz o sinal e recalcula o motivo usando o orçamento e a revisão histórica referenciada. A conta atual não é usada para reinterpretar a recusa.

A CLI existente aceita `--rejected-signal <rule_id> <sequence>`, além de `--input` offline. Formato novo: `gmgn-paper-admission-replay-v1`. Replays de um/dois lotes continuam compatíveis.

## Provas e testes

**386 testes aprovados, zero falhas/erros/skips, em 63,320 s**, na imagem final: 355 regressões e 31 novos casos.

As três provas de recusa usam preços sintéticos e PostgreSQL real em schemas privados:

| Caso | Estado na recusa | Motivo reproduzido |
|---|---|---|
| Caixa | Caixa 100, reserva 60, disponível 40; nova tentativa 60 USD | `Insufficient available paper cash` |
| Quantidade | Uma posição já ocupa o limite de uma; nova tentativa 60 USD | `Paper account position count limit` |
| Exposição | Caixa 200, reserva 60, limite de exposição 100; nova tentativa 60 USD | `Paper committed exposure limit` |

Em todos os casos, o replay usou a **revisão 2**, mesmo após a posição aceita expirar, liberar recursos e avançar a conta à revisão 3. O sinal recusado não foi tentado novamente após a retomada. Resultado: **zero movimentos de conta pela recusa**.

Os três bundles foram exportados e reproduzidos em Docker com `--network none`, sem credenciais; relatórios integralmente idênticos. Cada bundle contém duas avaliações e dois frames, totalizando 9.796 bytes de frames.

Os testes também cobrem saldo/motivo/revisão/hash alterados, sinal inválido, cursor incompatível, dados futuros, frame ausente, registro legado sem evidência, falha na gravação e perda de confirmação após commit. PostgreSQL recusou a tentativa de escrita dentro da transação read-only do exportador.

## Implantação e preservação

Imagem `midas-data-app:paper-admission-replay-v1-20260910`, SHA `fb23912add4898283073e7fff5569aea927d6e91492b617c32a2a231f5c08a04`. Somente a API foi recriada: container `a9b0941990e9`. Quatro módulos implantados conferem com o workspace. Tag salva no env operacional.

Aceite em **11/09/2026 às 02:11:52Z**:

- Seis serviços saudáveis; coletor, worker, PostgreSQL, NATS e ClickHouse mantiveram IDs/imagens.
- Conta na revisão 7, posições, históricos, três eventos de vínculo e demandas idênticos antes/depois.
- Zero posições abertas, regras/vínculos habilitados ou demandas paper.
- Relatórios dos ciclos reais de um/dois lotes iguais aos checkpoints anteriores.

A base real tem **zero recusas automáticas históricas**. Nenhuma foi forçada em live: a comprovação dos novos eventos é dos testes privados; a API implantada está preparada para registrá-los nas próximas recusas. Nenhuma migration, nova dependência ou ampliação de quota/coleta.

## Limites

Este recorte cobre recusas automáticas no cálculo da **conta**. Recusas HTTP manuais e gates anteriores — como sinal velho, janela ausente, posição já consumida e teto global de quatro lotes — não recebem essa evidência de conta e não são declarados reproduzidos.

Registros antigos sem `account_check` retornam `failed / historical account evidence missing`. O replay não deduz o saldo histórico pelo horário nem reescreve eventos antigos. A revisão anterior da conta continua sendo checkpoint confiado; não se reexecuta sua origem ou normalização dos brutos.

Limites de exportação: uma recusa, até 512 avaliações, 4 MiB por frame, 8 MiB de frames e 16 MiB por bundle. Leitura em transação `REPEATABLE READ, READ ONLY`, sem `Store.open`, migrations ou consulta a provedor.

## Reproduzir

Na raiz do workspace:

```powershell
$evidence=(Resolve-Path .scratch/checkpoint-paper-admission-replay-20260910).Path
docker run --rm --network none --entrypoint python -v "${evidence}:/evidence" midas-data-app:paper-admission-replay-v1-20260910 -m midas_data.paper_replay --input /evidence/cash-bundle.json --report /evidence/cash-offline.json
```

Trocar `cash` por `count` ou `exposure` para os outros casos. Exportar uma recusa existente requer `--rejected-signal <rule_id> <sequence> --export <bundle> --report <relatorio>` num processo com `DATA_DATABASE_DSN` configurado.

Evidências em `.scratch/checkpoint-paper-admission-replay-20260910/`: três bundles e relatórios online/offline, XMLs, `test-results.json`, `before-live.json`, `after-live.json`, `after-acceptance.json`, hashes implantados, runtime e backups anteriores.
