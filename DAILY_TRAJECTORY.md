# 4.3 — curva e resumo do dia UTC

> Atualização 4.4: [grade, passagens e reentradas](CROSSINGS.md) implantadas, preservando a resposta diária. O relatório abaixo registra o checkpoint original da 4.3.

**Concluída e implantada em 10/09/2026.** Checkpoint **CP-DAILY-01**, A0, sem subagentes. Próximo recorte: **4.4**, grade versionada de market cap, cruzamentos, reentradas e contexto.

## Contrato mínimo entregue

A rota autenticada `GET /v1/trajectory/daily` devolve a curva completa do dia no limite aceito, seus gaps e um novo objeto `daily`, versão `gmgn-daily-summary-v1`. Reutiliza a [série da 4.2](TRAJECTORY.md): fonte, chain, token, método, referências, contratos econômicos, revisões e política de lacunas permanecem iguais.

| Parâmetro | Regra |
|---|---|
| `source_kind`, `chain`, `token` | Obrigatórios; mesmo isolamento e normalização de identidade da 4.2 |
| `method` | Um dos três métodos existentes; capitalização reportada e derivada separadas |
| `day` | Dia de calendário UTC, em YYYY-MM-DD |
| `knowledge_cutoff` | Obrigatório e com fuso explícito; obter de /v1/clock |
| `max_points` | Padrão e máximo 2.000; exceder gera 413, sem resumo parcial apresentado como dia completo |
| `gap_after_seconds` | Mesmo parâmetro e defaults da 4.2: 720 s para token_info, 120 s para Trending/Trenches |

Intervalo do dia: **[00:00 UTC, 00:00 UTC seguinte)**. Para dia em andamento, o fim consultado é o menor entre a próxima meia-noite e o corte. Esse fim aparece em `through_at`. Horas ainda futuras aparecem como `not_elapsed_seconds`, sem virar gap de coleta. Dia que ainda não começou no corte, data inválida, corte sem fuso ou no futuro retornam 422.

O eixo continua **received_at da captura**, conforme contrato operacional da 4.2. A proposta ampla Q01 previa outros relógios/agregações; esta entrega mínima não promove horário de recepção a instante de negócio on-chain. Não usa últimas 24 h como substituto do dia UTC.

## Resumo e proveniência

| Campo em daily | Significado |
|---|---|
| `first_observation`, `last_observation` | Primeira/última amostra elegível; não afirmam valor exato à meia-noite |
| `minimum_observed`, `maximum_observed` | Extremos das amostras elegíveis daquele token/método/dia/corte |
| `calendar_state` | in_progress ou ended; descreve somente o calendário |
| `state` | available se houver amostra elegível; caso contrário unavailable |
| `has_known_revisions` | Há captura da curva com mais de uma interpretação conhecida no corte |
| `coverage` | Intervalo consultado, tempo ainda não decorrido, bordas, maior intervalo entre amostras/bordas, gaps sinalizados e interrupções |

As quatro marcas retornam valor decimal textual, horário, point_id/capture_id, versão de interpretação e anchor_fact_id. `matching_point_ids` preserva todas as referências que sustentam um extremo empatado. A curva contém os demais detalhes, hashes e operandos.

Primeira/última hora com valores diferentes e sem ordem comprovada deixa a respectiva marca indisponível por `ambiguous_observation_order`; não escolhe um valor pelo UUID. Se as amostras empatadas têm valor numérico igual, o valor é disponível e todas as referências são mantidas. O representante técnico segue a ordem determinística da curva, sem alegar ordem de mercado.

Ausência, valor inválido e alias não homologado permanecem indisponíveis. Zero realmente observado é preservado. O máximo é **observado no recorte**, não ATH histórico, extremo integral do mercado ou valor de candle da corretora. Não há soma de volumes rolling, preenchimento de gaps ou estimativa de tempo em faixa nesta etapa.

## Cobertura e revisões

A cobertura herda o universo da 4.2: capturas com fatos identificáveis daquele token publicados até o corte. Quantidade de pontos e pequenos intervalos não provam acompanhamento contínuo. Não foi introduzido percentual fictício de cobertura.

`leading_seconds` mede início do dia até a primeira amostra elegível; `trailing_seconds` mede a última até through_at. Quando não há amostras, ambos são nulos, e o gap cobre o intervalo consultado. `largest_sample_interval_seconds` inclui as bordas. `flagged_gap_seconds` soma somente intervalos sinalizados pela política configurada; não é medição de todo o tempo sem atividade ou sem cobertura de mercado.

Um dia encerrado **não congela os dados**. Revisões e capturas recebidas/publicadas depois podem mudar o resumo em um corte posterior. O corte anterior continua reproduzível. A revisão vazia de um pico remove sua elegibilidade; o próximo máximo vem dos pontos ainda válidos, sem resgatar uma interpretação ultrapassada.

## Implementação

Novo `midas_data/daily_trajectory.py`, com limites de calendário e agregação pura. A rota diária e a rota da 4.2 compartilham o mesmo leitor, validações e orçamento: dois leitores de contexto, timeout de consulta de 5 s, resposta de até 4 MiB. A rota antiga conserva resposta e defaults.

O resumo é calculado ao consultar. Reutiliza fatos e índices existentes, sem nova tabela, migration, serviço, cache, agendamento ou dependência. A API não relê todos os brutos; a auditoria individual continua no endpoint de valuation.

Referências de implementação conferidas na documentação oficial Python 3.13: [datetime e UTC](https://docs.python.org/3.13/library/datetime.html) e [comparação decimal exata](https://docs.python.org/3.13/library/decimal.html). Não foram usadas aritmética financeira com float ou inferências de fuso local.

## Testes e prova real

**67 testes aprovados**, zero falhas, erros ou skips, na imagem Python 3.13 do projeto. São 15 casos desta etapa e 52 regressões da série/valuation. A integração usa HTTP + PostgreSQL real em schema privado, com fixtures sintéticas; não injeta dados no schema de produção.

Provados: virada UTC com inclusão/exclusão exata, conversão de offset, ano bissexto e virada de ano, dia atual, dia vazio, zero, empates, precisão decimal, captura atrasada, pico revisado e invalidado, corte antigo estável, métodos/fontes separados, autenticação, limites e igualdade com a série original.

**Aceite real read-only**, com corte fixo `2026-09-10T19:28:44.831494Z`. Um token com histórico foi selecionado para cada dia, usando apenas capturas GMGN existentes:

| Dia UTC | Método | Observados | Elegíveis | Estado do dia | Tempo pontual |
|---|---|---:|---:|---|---:|
| 2026-09-09 | token_info | 18 | 18 | Encerrado | 0,502 s |
| 2026-09-09 | Trending | 147 | 147 | Encerrado | 0,735 s |
| 2026-09-09 | Trenches | 23 | 0 | Encerrado | 0,421 s |
| 2026-09-10 | token_info | 72 | 72 | Em andamento | 0,412 s |
| 2026-09-10 | Trending | 725 | 725 | Em andamento | 2,509 s |
| 2026-09-10 | Trenches | 0 | 0 | Em andamento | 0,027 s |

**962 pontos elegíveis**, mais 23 indisponíveis. As seis curvas ficaram idênticas ao resultado da API anterior à implantação no mesmo corte. Os quatro máximos disponíveis coincidiram com a valuation individual e tiveram SHA256 do bruto conferido. Fatos, capturas e registros imutáveis das interpretações selecionadas permaneceram iguais. Repetir as seis consultas diárias no corte fixo produziu respostas idênticas.

O caso com 725 pontos levou 2,51 s; os tempos são amostras locais, não SLA ou certificação de escala. Não foi feita medição sustentada nem otimização adicional nesta entrega. A frequência e quota da coleta permanecem as da agenda 3.4.

## Runtime e continuação

Somente API atualizada para `midas-data-app:daily-v1-20260910`, imagem `sha256:9740a7b451d105a2aa673e7d87b411fbd99dad180d714ab5a4626fc96c044db7`. Os cinco outros containers mantiveram IDs; os seis serviços ficaram saudáveis. Worker conserva tracking-v1, coletor tracking-v2. DATA_APP_IMAGE foi atualizada no env privado para futuras recriações compatíveis, preservando credenciais.

Evidências: `.scratch/checkpoint-daily-20260910/`, incluindo contract-tests.xml, before.json, seis resultados diários, live-acceptance.json, build/deploy, comparação de serviços e acceptance.json. Não houve chamada GMGN adicional de validação ou operação financeira.

**Próximo 4.4:** implementar a grade já especificada até 100M, cruzamentos e reentradas, com referências às observações e incerteza nos gaps. **4.5** fará o aceite integrado dos consumidores temporais. Interface e gráficos visuais continuam na etapa 7.2; aqui foi entregue a curva consultável na API.
