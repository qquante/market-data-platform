# 5.1.1 — features compartilhadas em janelas consultáveis

> Atualização posterior: [5.1.2 concluída — janelas ativas com publicação, vencimento e retomada](FEATURE_WINDOWS.md). O texto abaixo registra o escopo desta primeira parte; a consulta histórica continua disponível.

**Concluída e implantada em 10/09/2026**, CP-SHARED-FEATURES-01, A0, sem subagentes. **5.1 permanece em andamento.** Esta primeira parte entrega o núcleo de cálculo e reutilização; **5.1.2** conectará sua atualização à publicação e aos vencimentos.

Implementação pequena: [features.py](midas_data/features.py) e uma rota na [API](midas_data/api.py). Sem nova tabela, migration, dependência, serviço ou chamada GMGN. Reutiliza a trajetória aprovada na [4.5](TEMPORAL_ACCEPTANCE.md).

## Consulta e significado

`GET /v1/trajectory/features`, autenticada. Mesmos parâmetros da série: source_kind, chain, token, method, start, end, knowledge_cutoff; max_points padrão 500/máximo 2.000; gap_after_seconds opcional. Janela explícita **[start,end)**, até 24 horas, UTC, end ≤ corte ≤ agora. Pode representar uma janela móvel quando o consumidor avança suas fronteiras.

A resposta conserva toda a série e acrescenta `features`, com versão `gmgn-window-features-v1`, frame_id, janela, dependências, disponibilidade necessária, qualidade e quatro valores:

| Feature | Cálculo e limite |
|---|---|
| market_cap_latest | Última observação dentro da janela; indisponível se ausente, inválida, empatada ou antiga demais em relação ao fim da janela. Não busca um valor anterior para preencher uma observação inválida. |
| market_cap_min_observed | Menor market cap elegível observado; inclui IDs de todas as amostras empatadas. Gaps continuam explícitos na qualidade. |
| market_cap_max_observed | Maior market cap elegível observado, com a mesma política de evidência. Não é ATH nem extremo contínuo do mercado. |
| market_cap_sample_change_pct | 100 × (última amostra − primeira amostra) / primeira amostra; exige duas amostras, denominador não zero e ausência de pontos indisponíveis, empates ou gaps detectados, incluindo bordas. |

A variação usa os horários das amostras e informa sua distância em segundos. Não interpola os valores nas fronteiras, não transforma ticks em segundos e não equivale a retorno executável ou alta causada apenas por preço. Alterações de supply e diferenças entre métodos conservam as restrições da valuation.

Valores originais e extremos preservam Decimal/texto. A variação faz subtração e escala exatas antes de dividir com **34 dígitos significativos e ROUND_HALF_EVEN**, independentes da precisão, arredondamento e traps do chamador. Essa divisão pode arredondar; a política está na resposta. Referência oficial: [Decimal Context](https://docs.python.org/3.13/library/decimal.html#decimal.Context).

## Compartilhamento e invalidação

Há dois cálculos: **última observação** e **estatísticas da janela**. Suas chaves incluem operador/versão, origem, identidade, método, unidade, relógio, escopo de pool, fronteiras, política de gap e revisões dos pontos usados. O frame referencia essas chaves e os pontos/fatos disponíveis na própria série.

Uma revisão anterior pode recalcular as estatísticas e reutilizar o último valor. Outro corte de conhecimento com exatamente as mesmas dependências reutiliza ambos. Mudança de dado, revisão, qualidade ou fronteira gera nova identidade. O corte consultado permanece no envelope; frame_id identifica os cálculos/dependências, não uma decisão histórica consumida ou persistida.

O cache pertence ao processo da API: até **128 entradas e 2 MiB de valores serializados**, com descarte por uso recente. Esse limite não é um teto de RAM total. Chaves têm tamanho fixo; resultados são devolvidos como cópias para um consumidor não alterar o valor de outro. Descarte ou processo novo apenas exige recálculo; a mesma entrada reproduz o mesmo frame_id. Referência oficial: [OrderedDict](https://docs.python.org/3.13/library/collections.html#collections.OrderedDict).

O cabeçalho `X-Midas-Feature-Cache` indica `reused=2; computed=0` quando os dois cálculos foram reutilizados. Não altera o corpo reproduzível nem afirma que a consulta não custou I/O.

**Limite desta parte:** cada requisição ainda lê a série limitada no PostgreSQL e verifica suas dependências. Compartilha os cálculos derivados; não elimina ainda leitura SQL/valuation/hash por requisição. O relógio e as fronteiras são reavaliados ao consultar. Não há timer ou consumidor de eventos mantendo janelas atualizadas em segundo plano. Esse é o trabalho da 5.1.2, antes de ligar múltiplos consumidores automaticamente.

## Validação

**115 testes aprovados**, zero falhas/erros/skips, na imagem Docker Python 3.13 candidata antes da implantação. São 21 novos casos de features, um caso adicional do limite HTTP e as 93 regressões anteriores, incluindo integração ampliada.

- Vinte leituras iguais reutilizam os dois cálculos: duas computações iniciais, 40 reutilizações. É teste funcional de compartilhamento, não 20 estratégias reais ou benchmark.
- Revisão seletiva, corte antigo, isolamento por identidade/método, expiração sem tick, saída de pontos da janela, falta de dados, empate, zero, gap, precisão extrema, descarte e reconstrução passam.
- Integração com PostgreSQL real em schema privado amplia o cenário da 4.5: chegada tardia, revisão, invalidação e quatro cortes reproduzíveis, com comparação às séries e limites de autenticação/pontos/intervalo.
- Rota nova participa dos limites existentes de dois leitores, 429 e 413 acima de 4 MiB. Timeout de leitura de 5 s e recuperação do pool seguem validados pelo helper compartilhado; não há SLA de latência total.

Na primeira rodada, a serialização do hash não aceitava UUID nativo das fixtures. Corrigida a conversão canônica para texto; ambos agora produzem a mesma chave. Também corrigida uma expectativa de teste numérico que ignorava o arredondamento declarado. Evidências iniciais preservadas; nenhuma imagem com esses casos pendentes foi implantada.

## Prova real e runtime

Conferência read-only às **2026-09-10T20:45:32.613758Z**, corte **20:02:26.964021Z**, usando três janelas curtas sobre capturas já persistidas do token Solana `9HsYGS4H6RoTjBNiV4G38NzKVrXiWhkgCPMWrChZpump`.

| Método | Pontos | Resultado |
|---|---:|---|
| token_info | 3 elegíveis | Último, extremos e variação conferidos; cálculo percentual comparado com fração racional independente |
| Trending | 3 elegíveis | Mesmas conferências; método permanece separado |
| Trenches | 3 indisponíveis | Quatro features indisponíveis, sem fabricar valores |

Nas três janelas a primeira consulta calculou dois resultados; a segunda reutilizou ambos, com corpo/frame_id idênticos. Duas valuations individuais e o SHA256 dos brutos confirmaram os últimos valores. As três séries preservadas antes do deploy permaneceram iguais, assim como nove respostas anteriores de série/diário/passagens sobre o dia UTC já auditado.

Somente a API foi atualizada para `midas-data-app:features-v1-20260910`, SHA `f1744560eb10e6dd80c44e469e68a761fc4614c278d583fbe7e38eca98e0f2b2`. Os cinco outros containers mantiveram IDs/imagens; os seis ficaram saudáveis. Código anterior da aplicação alterado somente em api.py, mais o novo features.py. Coletor, worker, migrations e quota preservados.

Evidências: [checkpoint-features-20260910](../.scratch/checkpoint-features-20260910/), [testes finais](../.scratch/checkpoint-features-20260910/contract-tests.xml), [prova real](../.scratch/checkpoint-features-20260910/live-acceptance.json), [runtime](../.scratch/checkpoint-features-20260910/runtime-acceptance.json). O diretório também contém baselines, scripts, build.log e a rodada inicial. Testes persistentes em [test_features.py](tests/test_features.py) e nos dois arquivos de aceite temporal.

## Próximo recorte

**5.1.2:** alimentar uma janela compartilhada mínima por publicações e vencimentos, consultar os dados uma vez por atualização compatível e entregar o mesmo frame aos consumidores. Preservar cortes/revisões e invalidar frescor sem exigir novo preço. Definir e provar a retomada do estado consumido antes de afirmar atualização contínua ou integrar regras.

Depois vem **5.2**, regras versionadas com true/false/unknown. Estratégias, posições paper e medições extensas não foram antecipadas nesta parte.
