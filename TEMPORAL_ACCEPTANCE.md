# 4.5 — aceite integrado das consultas temporais

**Concluída em 10/09/2026.** Checkpoint **CP-TEMPORAL-ACCEPTANCE-01**, A0, sem subagentes. Fecha a base temporal mínima do [mapa numerado](../TASKS.md#mapa-numerado-da-refatoração--10092026), até 4.5. Próximo recorte: **5.1**, features e janelas compartilhadas.

A implementação das etapas [4.2](TRAJECTORY.md), [4.3](DAILY_TRAJECTORY.md) e [4.4](CROSSINGS.md) passou no aceite integrado. Esta entrega acrescenta dois arquivos de testes e a documentação; o código da aplicação, migrations, configuração e imagens implantadas permaneceram iguais. Nenhuma medição extensa, nova coleta, estratégia ou execução financeira foi acrescentada.

## O que foi aceito

| Critério | Prova |
|---|---|
| Série, dia UTC, passagens e contexto usam o mesmo conhecimento | Mesmos pontos/fatos/valores nas consultas, contexto igual à valuation individual e resumo conferido por Decimal |
| Publicação atrasada não aparece em corte anterior | Uma captura recebida antes da meia-noite é publicada depois; altera somente consultas com novo corte |
| Revisão e invalidação substituem a interpretação inteira | Pico corrigido e interpretação posterior sem fatos; valor invalidado fica indisponível, sem reaproveitar o antigo |
| Cortes anteriores permanecem reproduzíveis | Quatro conjuntos completos de respostas repetidos após as alterações e comparados exatamente |
| Virada UTC e lacunas mantêm a semântica | Passagem atravessa meia-noite no recorte contínuo; cada resumo respeita seu dia; gap e dado ausente interrompem a comparação |
| Leitores limitados e reutilizáveis | Duas consultas admitidas, terceira retorna 429 com Retry-After: 1; após liberação a consulta funciona |
| Resposta excessiva não vira resultado parcial | Nas três rotas, resposta acima de 4 MiB retorna 413; rejeições repetidas não esgotam as vagas |
| Espera não prende o leitor indefinidamente | Pool PostgreSQL privado ocupado causa 503 no timeout de 5 s; o mesmo app/pool volta a servir todo o conjunto |

**93 testes aprovados**, zero falhas, erros ou skips, Docker Python 3.13, executados contra a imagem que já estava implantada. São cinco novos casos e 88 regressões temporais/econômicas.

Os testes de concorrência/tamanho usam leitores controlados e resposta artificial; são provas do contrato HTTP. O teste integrado usa PostgreSQL real, schema privado e capturas sintéticas. O timeout ocupa apenas o pool privado de uma conexão, sem segurar o lock global de publicação. O schema é removido no final; nenhuma fixture é inserida no schema de produção. Contagens de capturas, interpretações, fatos e outbox permanecem iguais antes/depois das consultas do cenário.

O limite de 5 s cobre a espera assíncrona de leitura. Não é um SLA para projeção e serialização síncronas nem certificação de latência sob carga. O limite de 4 MiB é conferido no corpo serializado; não é um teto de memória do processo. Referências oficiais: [asyncio.timeout](https://docs.python.org/3.13/library/asyncio-task.html#asyncio.timeout) e [asyncio.Semaphore](https://docs.python.org/3.13/library/asyncio-sync.html#asyncio.Semaphore).

## Conferência do histórico real

Somente leitura da API local, em **2026-09-10T20:29:25.951055Z**. Origem GMGN, Solana, token `Hg5Ja55T5wESq4vyFoiVCMeHXtGyVA69X2UHq8hgpump`, dia UTC **09/09/2026**, corte fixo **2026-09-10T20:02:26.964021Z** preservado da etapa 4.4.

| Método | Pontos | Elegíveis | Passagens inferidas | Reentradas |
|---|---:|---:|---:|---:|
| token_info | 18 | 18 | 35 | 8 |
| Trending | 147 | 147 | 115 | 43 |
| Trenches | 23 | 0 | 0 | 0 |
| Total | 188 | 165 | 150 | 51 |

Foram **14 leituras HTTP**: série, resumo e passagens dos três métodos; duas valuations individuais e seus brutos; status. As três rotas compartilham exatamente a mesma série quando consultadas no mesmo intervalo, método e corte. Todas as nove respostas também coincidiram com o registro anterior no corte fixo.

Extremos e primeira/última observações foram conferidos diretamente nos pontos. Uma enumeração independente dos pares consecutivos e dos níveis reproduziu as 150 passagens; contextos referenciados coincidiram com os valores dos pontos. Dois contextos completos coincidiram com a valuation individual e seus brutos passaram no SHA256.

Os 23 pontos Trenches não homologados continuam indisponíveis nas três consultas, sem extremos ou eventos artificiais. Essa limitação semântica permanece explícita. Não houve chamada adicional à GMGN, reprocessamento ou mudança de quota.

## Runtime e evidências

Os **seis containers mantiveram IDs e imagens**, todos saudáveis. Os hashes dos **29 arquivos da aplicação/migrations** permaneceram iguais. Nenhum build, deploy ou restart nesta etapa. API permanece em `midas-data-app:crossings-v1-20260910`, SHA `65efd723878944362b71e0f4a00e196c1a98daa416f972cc5f0a0e8c4cd78916`; worker e coletor continuam nas imagens da agenda 3.4.

Testes persistentes: [limites HTTP](tests/test_temporal_acceptance.py) e [aceite integrado PostgreSQL](tests/test_temporal_acceptance_integration.py).

Evidências em [checkpoint-temporal-20260910](../.scratch/checkpoint-temporal-20260910/):
- [contract-tests.xml](../.scratch/checkpoint-temporal-20260910/contract-tests.xml): 93 casos finais; a rodada inicial de cinco casos também está preservada.
- [live-acceptance.json](../.scratch/checkpoint-temporal-20260910/live-acceptance.json), live-bundle-3/4/5.json e [live_probe.py](../.scratch/checkpoint-temporal-20260910/live_probe.py): consultas e comparação real.
- [runtime-acceptance.json](../.scratch/checkpoint-temporal-20260910/runtime-acceptance.json), services-before/after.jsonl, runtime-images.txt e application-before.json: integridade e serviços.

A regressão executou os arquivos test_temporal_acceptance*, test_crossings*, test_daily_trajectory*, test_trajectory*, test_valuation.py, test_economic_inputs.py e test_valuation_integration.py, montados em /app/tests:ro no serviço verify da imagem atual, com DATA_INTEGRATION=1. Não é a suíte completa de todos os módulos do repositório.

## O que falta

**5.1** é a próxima entrega: definir uma primeira feature/janela compartilhada sobre estes dados e reaproveitar seu resultado enquanto as dependências forem as mesmas, mantendo corte, método, revisão e indisponibilidade explícitos. Escolher o conjunto mínimo ao iniciar essa etapa; nenhuma camada de cache ou tabela foi antecipada aqui.

O aceite 4.5 não fecha toda a especificação ampla M2/Q01–Q14: episódios globais, primeira passagem de toda a vida do token, expansão de famílias/catálogo, estratégias paper, interface e capacidade sustentada seguem nos respectivos recortes do mapa. Não houve nesta etapa novo aceite comparativo de consultas temporais em DuckLake/ClickHouse, nova prova de restart ou promessa de resposta instantânea. As medições extensas continuam adiadas conforme pedido do usuário.
