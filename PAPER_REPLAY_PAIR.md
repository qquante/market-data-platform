# Replay de dois lotes — CP-PAPER-REPLAY-02

> Continuação concluída: [5.4.3 — replay de recusas da conta](PAPER_ADMISSION_REPLAY.md), 386 testes e API atualizada. Próximo 6.1; carga 20/100 posterior. O registro abaixo descreve o checkpoint anterior.

**5.4.2 concluída em 10/09/2026.** A etapa 5.4 permanece em andamento. Próximo **5.4.3**: incluir no replay as admissões recusadas por saldo/limites, com poucos casos isolados. Carga 20/100 continua posterior.

## Mudança

O mesmo verificador de regras e posições atende um lote ou exatamente dois lotes terminais da conta main. Cada lote gera seus movimentos calculados; a conta aplica esses movimentos na **ordem global das revisões do ledger**, comparando reserva, débito, crédito e saldo intermediário. Uma posição é finalizada antes de a outra nos cenários de teste.

O exportador lê o intervalo completo da conta entre o primeiro e o último movimento selecionado, em uma transação PostgreSQL `REPEATABLE READ, READ ONLY`. Não filtra silenciosamente movimentos de uma terceira posição. Lacunas, movimentos extras, crédito repetido, ordem incorreta e referência errada de posição/revisão impedem aprovação.

Formato novo `gmgn-paper-replay-pair-v1`, com dois lotes e um mapa compartilhado de frames. O bundle individual `gmgn-paper-replay-v1` continua aceito; o relatório do ciclo real da 5.4.1 permaneceu integralmente idêntico.

Código alterado nesta etapa: somente `midas_data/paper_replay.py`. Novo teste: `tests/test_paper_replay_pair_integration.py`. Nenhuma migration, dependência, serviço ou ajuste de quota.

## Evidências

| Prova | Resultado |
|---|---|
| Testes na imagem final | **355 aprovados**, zero falhas/erros/skips, 58,301 s |
| Casos novos | 25, além das 330 regressões |
| Par intercalado | PostgreSQL privado, preços sintéticos, duas reservas, duas entradas e duas saídas em ordem inversa à admissão |
| Par misto | Uma entrada expira enquanto o outro lote abre/fecha; saldo reconciliado |
| Recuperação | Dono recarregado entre entrada e saída; nova recuperação e repetição da admissão não duplicam crédito |
| Escrita pelo exportador | PostgreSQL recusou a tentativa no teste privado |
| Dois ciclos reais já existentes | 7 avaliações, 52 eventos paper, 6 movimentos e 47 frames reproduzidos |
| Offline | Par sintético e par real geraram relatórios idênticos com `--network none`, sem credenciais |

Os **dois ciclos reais ocorreram em sequência**. A prova de interleaving é dos testes com preços sintéticos, usando o mesmo runtime online e SQL real em schemas privados; nenhuma nova posição foi ativada na conta real.

No bundle sintético salvo, a sequência global foi: reserva A, reserva B, entrada A, entrada B, saída B, saída A. Quatro frames compartilhados, total de 14.891 bytes. Saldo simulado final `116.5055329062317996505532906231799632384` USD, com reserva e custo investido zerados. SHA canônico: `977cbe4cc711bfe83cc93b5542ec296bea15a937cfbf9a7c76d26cd5c23636fc`.

No par real, foram reexecutadas as revisões 2–7 da conta, partindo do checkpoint de inicialização na revisão 1. Os 47 frames somam 465.908 bytes. Saldo final reproduzido: `84.43999830569181152166612996038074110159817416` USD. SHA canônico: `f8c2314cfb90be6ffeb59cc4aba6ab78864882bebd606ac8e1f38032c760d84d`.

## Runtime e limites

Imagem auxiliar construída e executada: `midas-data-app:paper-replay-pair-v1-20260910`, SHA `7df6da3ce99dcbaca64ab000c3effd63f39bc44cb9320f62a5fcc5c140bc9a90`. Os dois arquivos alterados conferem com a imagem.

Aceite real em **11/09/2026 às 01:36:53Z**: conta, posições, históricos e demandas iguais ao checkpoint anterior. Zero posições abertas, regras/vínculos habilitados ou demandas paper. Seis serviços saudáveis, mantendo IDs e imagens; API continua em `paper-prices-v2-20260910`. Nenhum serviço reiniciado.

Limites conservados: até 512 avaliações e 512 eventos por lote, no máximo seis movimentos da conta no par, 4 MiB por frame, 8 MiB de frames no total e 16 MiB por bundle. A seleção deve conter dois IDs distintos. Um terceiro lote dentro do intervalo causa recusa; o histórico não é parcialmente aprovado.

O checkpoint anterior continua sendo uma entrada confiada. A reprodução usa frames e horários registrados, sem reexecutar normalização dos brutos ou provar ticks ausentes. Admissões recusadas ainda não integram o replay; são o próximo recorte pequeno. Cenários 20/100 e capacidade sustentada não foram medidos.

## Reproduzir

Na raiz do workspace:

```powershell
$evidence=(Resolve-Path .scratch/checkpoint-paper-replay-pair-20260910).Path
docker run --rm --network none --entrypoint python -v "${evidence}:/evidence" midas-data-app:paper-replay-pair-v1-20260910 -m midas_data.paper_replay --input /evidence/fixture-pair-bundle.json --report /evidence/fixture-pair-offline.json
```

Para o par real, trocar os arquivos por `live-pair-bundle.json` e `live-pair-offline.json`. Para exportar dois lotes existentes, a CLI aceita `--position-ids <id-a> <id-b> --export <bundle> --report <relatorio>` em processo com `DATA_DATABASE_DSN` já configurado; sem escrita no banco.

Evidências em `.scratch/checkpoint-paper-replay-pair-20260910/`: bundles/relatórios online e offline, `single-compatibility.json`, `acceptance.json`, `contract-tests.xml`, `test-results.json`, `runtime-verification.json`, `image-files.json`, build e backups anteriores.
