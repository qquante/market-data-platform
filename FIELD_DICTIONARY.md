# Dicionário do fluxo GMGN — CP-BASE-FIELDS-08

> Este checkpoint não encerra a etapa ampla de dados. Diretriz e pendências vigentes: [CP-DATA-SCOPE-09](DATA_SCOPE_AND_ACCEPTANCE.md).

11/09/2026. Entrega instalada em `midas-data-base`: catálogo operacional das cinco fontes atuais, avaliação de qualidade na leitura e apresentação no painel. Estratégias continuam adiadas.

## O que está fechado

- **831 caminhos**: 805 observados em **200 respostas originais** (40 por endpoint no corte de 16:20:54 UTC) e 26 previstos pelos adapters, ausentes nessa amostra. São caminhos JSON; categorias repetidas de Trenches, estruturas e metadados não são novas variáveis econômicas independentes.
- Toda entrada tem endpoint/caminho, categoria, significado ou incerteza explícita, grão, tipos observados/aceitos, unidade, janela, política de ausência, uso e referência ao adapter quando existe.
- **135 caminhos de métricas com contrato**, **329 metadados**, **58 estruturas**, **309 bloqueados para cálculo**. Ter entrada no dicionário nunca aprova uma ocorrência automaticamente.
- JSON original, números exatos, null, zero, strings vazias e listas vazias preservados. Campos novos ou mudanças de tipo ficam visíveis e bloqueados; busca/paginação não escondem problemas no restante da resposta.
- Consulta em `/v1/dictionary`, conferência recente em `/v1/dictionary/coverage`, interpretação por ocorrência em `/v1/explorer/captures/{id}/fields`. Painel **Dados → Dicionário das cinco fontes** com filtros, páginas, unidade, significado, uso, contratos e links para capturas.
- Nenhuma migração, reescrita de fatos históricos, nova projeção econômica ou chamada adicional à GMGN. A coleta e a quota continuam como antes.

## Por que ainda existem campos bloqueados

Os 309 caminhos bloqueados dividem-se em **115 descritos pelo provedor sem projeção numérica homologada**, **53 contratos existentes que mantêm ressalvas semânticas**, **140 campos somente observados** e **1 divergência documental**. Incluem caminhos repetidos entre categorias; não são 309 falhas da coleta.

Exemplos: `market_cap` de Trenches não vira automaticamente `usd_market_cap`; supply sem escala comprovada não é dividido por decimals; `mk`, `co_d` e `s_qsafe` permanecem extensões opacas. A referência de holders descreve `profit` como total na tabela e realizado na ordenação: o conflito foi registrado. O saldo `native_balance` de holders permanece em **lamports**, não SOL.

A base está pronta para guardar, encontrar, inspecionar e selecionar dados com qualidade explícita. **Não está declarada homologação econômica de todos os campos da GMGN.** Quando uma pesquisa precisar de um dos campos documentados ainda sem projeção, deve acrescentar contrato e adapter versionados, com janela/unidade/identidade/ausência e evidência específicas. Não vale transformar todos os metadados em fatos numéricos por antecipação.

## Arquitetura e limites

Dicionário estático empacotado, aproximadamente 0,91 MB em JSON compacto, carregado uma vez. Lookup por `(endpoint, schema_path)`; o walker insere `*` somente em índices de arrays reais. A versão `gmgn-flow-dictionary-v1` e seu hash identificam a interpretação atual. Alteração após publicação exige nova versão; não regenerar uma versão publicada com novas regras silenciosamente.

A elegibilidade de cálculo exige rede Solana, interpretação publicada até o corte solicitado, captura aceita pelo adapter, ID de contrato compatível, qualidade `valid`, tipo esperado e presença do valor. Metadados e estruturas têm função descritiva. A ocorrência ausente/invalidada/bloqueada não vira zero e não ganha elegibilidade por receber um nome no catálogo.

O dicionário é uma camada de leitura; não reclassifica os fatos v4, holders v2 nem candles v1 já gravados. O corte continua selecionando a versão publicada do adapter. A descrição do dicionário é **atual**, explicitamente separada da disponibilidade histórica da evidência.

A conferência operacional lê no máximo uma captura GMGN Solana recente de cada fonte, com cache de 30 s, espera limitada, 4 leitores admitidos e os mesmos 2 slots compartilhados de leitura pesada. Consulta do dicionário é paginada, máximo 100 linhas, sem banco. Conferência tem timeout de 10 s; o explorador conserva seus limites de bytes/nós/profundidade/página. Uma captura recém-recebida pode aparecer `pending`: nesse intervalo nenhum valor recebe liberação de cálculo.

Completo aqui significa **todos os caminhos desta baseline têm disposição e qualquer desvio consultado é explícito**. Não significa scan integral do histórico, todos os tokens acompanhados, todos os endpoints GMGN, nem monitor persistente de cada extensão sem consulta. A conferência roda quando o painel/endpoint é consultado; não cria automação horária.

## Verificação e resultado

- **958 testes únicos passaram** entre a suíte empacotada, SQL e regressões focadas, descontando repetições. Skips não contados como aprovação. A suíte ampla foi executada antes do acréscimo apenas descritivo de contagem de motivos; as 28 regressões focadas passaram novamente na imagem final.
- SQL executado em schema privado: fonte e corte isolados, adapter publicado exigido, qualidade por ocorrência e desvio fora do filtro ainda visível. Fonte do teste é fixture; não se apresenta como coleta real.
- **200 capturas reais, 1.281.664 ocorrências** conferidas com hash original: zero caminhos fora da baseline, zero mudanças de tipo. **11.320 ocorrências inválidas e 114.059 ausentes continuam preservadas e excluídas**; não são 11.320 tokens ruins. Exemplos recentes incluem timestamps zero rejeitados, contagens negativas e barras fora da faixa solicitada.
- Leitura de cada captura com SQL/raw/interpretação: medianas aproximadas ranking 9,7 ms; token_info 3,2 ms; candles 4,2 ms; holders 120,4 ms; Trenches 364,0 ms. Maior observação cerca de 1 s. Não é ensaio de carga sustentada.
- HTTP local: busca do dicionário mediana **5,1 ms** / máximo 47,2 ms em 20 consultas. Conferência das cinco fontes sem cache **309 ms** nessa observação. Oito leituras simultâneas com cache aquecido: mediana 5,7 ms / máximo 6,2 ms. Não mede oito leituras pesadas simultâneas.
- **20 verificações reais de navegador**: dicionário, filtros, conflito documental, paginação, dados originais, download SHA-256, holders, candles, resultados existentes, seis áreas desktop/mobile, recarga sem chave, nenhuma Authorization enviada e nenhum erro JS/5xx. Validação funcional/DOM; não foi emitido screenshot.
- API e worker atualizados para `midas-data-app:field-dictionary-v1-20260911`. Seis ledgers paper idênticos antes/depois; zero regras, vínculos ou posições ativos. Coletor e executores paralelos não reiniciados por esta entrega.

A cadência da fonte não mudou: slots de aproximadamente 60 s por família; o acompanhamento individual obedece à agenda e à quota. Resposta rápida do painel não significa atualização instantânea de todo o mercado. Capacidade sustentada, retenção e validação de estratégia não são certificados por esta rodada.

## Fechamento em operação

Snapshot final em **2026-09-11 16:44:27 UTC**: seis serviços saudáveis, 8.470 capturas GMGN prontas e 22 fixtures separadas. As cinco capturas recentes conferidas estavam prontas, sem novos caminhos ou mudança de tipo. Normalizador sem fila; ClickHouse e lake tinham dois itens correntes, com cerca de 3,6 s, sem erro. Não se declara fila permanentemente vazia durante coleta contínua.

Imagem final de API/worker: `sha256:19a114124be8b71dccafeb2cbd4b5b5edadd4655c8e02a670a0029c077171c4d`. Oito arquivos de runtime, incluindo dicionário e interface, conferidos por SHA-256 contra a fonte local. Coletor manteve início em 11/09 às 11:16:26 UTC e os três serviços de armazenamento/transporte mantiveram seus inícios de 10/09. Resultado em `outputs/gmgn-field-dictionary-20260911/runtime-final.json`.

## Arquivos e reprodução

- [Dicionário completo em tabela](../outputs/gmgn-field-dictionary-20260911/DICIONARIO_COMPLETO.md) e [JSON exportado](../outputs/gmgn-field-dictionary-20260911/field-dictionary.json).
- [Inventário das 200 capturas](../outputs/gmgn-field-dictionary-20260911/inventory-200-captures.json), [conferência da amostra](../outputs/gmgn-field-dictionary-20260911/sample-verification.json), [HTTP e cobertura recente](../outputs/gmgn-field-dictionary-20260911/http-verification.json), [testes](../outputs/gmgn-field-dictionary-20260911/checks-summary.json) e [navegador](../outputs/gmgn-field-dictionary-20260911/browser-acceptance.json).
- Fonte empacotada: `midas_data/field_contracts/gmgn-flow-dictionary-v1.json`; leitor: `field_dictionary.py`; rotas: `field_dictionary_api.py`; integração de ocorrências: `explorer.py`. Gerador: `scripts/gmgn-build-field-dictionary.py --inventory <inventário congelado>`, com `PYTHONPATH=data_platform`.
- Referências oficiais fixadas no commit `4e48e25328c114eb615a3419599366276479cd42`: [token](https://github.com/GMGNAI/gmgn-skills/blob/4e48e25328c114eb615a3419599366276479cd42/skills/gmgn-token/SKILL.md) e [market](https://github.com/GMGNAI/gmgn-skills/blob/4e48e25328c114eb615a3419599366276479cd42/skills/gmgn-market/SKILL.md). Conteúdo e hashes da consulta em `.scratch/field-dictionary-20260911/`; não foram seguidas recomendações de trading desses documentos.
- Evidência técnica: `.scratch/field-dictionary-20260911/` (before/final, XML, fonte anterior, aplicação, leitura de amostra, HTTP e browser). Backup privado apenas da configuração anterior para reversão da imagem; não é novo backup de dados nem prova de restore.
