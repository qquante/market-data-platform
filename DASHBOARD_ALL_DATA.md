# Dashboard completo dos dados disponíveis

> Estado CP-BASE-READY-07: [correção dos candles e limites atuais](BASE_READINESS.md); [inventário completo dos dados coletados](INVENTARIO_DADOS_COLETADOS.md). Estratégias adiadas pelo usuário.

> Acesso local (11/09/2026): dashboard carrega sem chave em http://127.0.0.1:8791/. Consultas GET/HEAD liberadas por DATA_DASHBOARD_PUBLIC_READ; alterações continuam autenticadas. [Contrato de acesso](DASHBOARD.md#pesquisa-e-credencial).

CP-DASHBOARD-ALL-05, implantado em 11/09/2026. Acesso: http://127.0.0.1:8791/ (seis áreas; links diretos por hash, por exemplo `/#data`).

## Entrega

| Área | Dados e navegação disponíveis |
|---|---|
| Operação | Saúde, frescor, filas, recursos, registro de tokens, contagem de capturas separada por origem/qualidade, estado operacional completo, famílias e falhas. |
| Tokens | Todas as identidades do registro por páginas, fonte/cadeia/contrato, corte UTC, métricas conhecidas incluindo unverified, histórico por métrica, proveniência e acesso à captura de inscrição ou trajetória. |
| Dados | Histórico paginado de capturas, filtros de origem/cadeia/família/estado/período; metadados, interpretações, problemas e recibos; todos os campos originais com busca/páginas; fatos normalizados com qualidade/unidade/contrato; catálogo de campos; download original byte a byte com hash. |
| Trajetórias | Curva, lacunas, marcos e evidência já existentes. Holders e OHLCV têm leitura estruturada na área Dados: carteiras/campos completos e gráfico de candles com tabela paginada de preço/volume/estado. |
| Pesquisa | 319 estratégias preservadas, critérios/saídas e métricas históricas em tabelas; coortes com cobertura/labels e todos os membros; comparações por train/validation/test; decisões, lotes, ledger e estudos em tabelas por seção, com detalhes e downloads. |
| Paper | Conta e movimentos, regras/avaliações/frames, vínculos/eventos, posições/ledgers, janelas compartilhadas e demandas de acompanhamento. Consulta somente a base nova. |

A navegação principal mostra seis áreas; listas extensas e tabelas largas usam paginação e rolagem local. Cartões monetários e gráficos arredondam apenas a apresentação; valores exatos continuam nos detalhes/tabelas/arquivos. `null`, zero, false, strings numéricas e listas/objetos vazios permanecem distintos no explorador de campos originais.

O painel mostra **o que a base já armazena**. Não cria dados ausentes, não importa automaticamente todo o legado e não certifica uma estratégia. No snapshot real de 12:47:42 UTC: **50.173 tokens GMGN Solana**, 7.194 capturas GMGN normalizadas, 92 em quarentena e 22 capturas fixture separadas. Esses números são uma amostra datada; o painel consulta contagens atualizadas.

Quarentena é estado do normalizador principal. Capturas OHLCV podem ter leitura própria válida, preservada em paralelo ao estado do normalizador. Processo saudável e frescor são indicadores distintos: nessa amostra havia seis serviços saudáveis, filas de processamento/entrega vazias e seis dos dez tokens ativos com consulta fresca; quatro aguardavam primeira consulta após admissão. O coletor marcou `source_current=false`, e o painel conserva esse estado.

## Consultas acrescentadas

- `GET /v1/explorer/captures`: origem obrigatória, intervalo de até sete dias, corte de conhecimento e filtros; página até 200. Cursor composto `recorded_at + capture_id` em ordem decrescente. Reutiliza o índice existente; nenhum índice/migration foi criado.
- `GET /v1/explorer/captures/{id}/fields`: bruto conhecido para a origem/corte, números lexicais exatos, busca por caminho/valor e página até 200. Até 4 MiB, profundidade 32, 50.000 nós e 1 MiB de resposta por página. JSON ambíguo/inválido ou acima do orçamento não é truncado silenciosamente; a resposta original continua acessível pelo download.
- `GET /v1/explorer/captures/{id}/facts`: todas as qualidades da interpretação mais recente publicada no corte, por sequência, até 200 fatos/página. Campo NUMERIC auxiliar não é exposto como substituto do texto exato.

As demais telas usam APIs já existentes. Leituras não chamam GMGN nem registram coleta, regras, bindings, ordens ou novos estudos automaticamente. Campos originais compartilham os dois slots de leitura de evidência; consultas novas têm timeout de cinco segundos. A API continua autenticada, chave apenas em memória da página, CSP self e texto da fonte inserido com textContent. O bruto é baixado como bytes, sem conversão UTF-8 intermediária.

O estado de normalização na lista de capturas e o resumo do catálogo de campos são estados operacionais atuais, identificados assim na interface. Não são reconstrução histórica de status. As consultas de fatos/campos respeitam o corte explícito; datas de evento, recepção e disponibilidade permanecem separadas.

## Validação

- 18 testes locais aprovados, incluindo nove novos casos de precisão, paginação, filtros, limites, campos vazios e autenticação.
- Imagem: **944 testes aprovados, zero falhas/erros, 33 skips**. Um teste SQL real adicional passou em schema privado: duas páginas sem repetição, filtro por endpoint, isolamento fixture/GMGN, corte anterior recusando captura futura e decimal longo preservado. **945 testes únicos aprovados** somando suíte empacotada e esse SQL; os 18 locais se sobrepõem. Integrações SQL alheias e backup host não foram reexecutados.
- Navegador Chromium real, 1440×1050 e 390×844: 13 fluxos conferidos, todas as seis áreas, tokens/capturas/campos paginados, download bruto com SHA-256, holders, candles, coorte real de 300 membros, nove linhas de comparação (3 estratégias × 3 períodos), três estudos, histórico das estratégias e paper. Zero erros JavaScript, zero respostas 5xx e zero overflow da página na execução final.
- A primeira suíte local detectou um decorator de rota estática incorreto; foi corrigido antes da implantação. O primeiro ensaio browser detectou overflow dos cartões paper por valores decimais extensos; a grade e a apresentação foram corrigidas. A imagem final preserva o mesmo Python aprovado; mudanças posteriores foram de interface e tiveram nova execução browser.
- A revisão automática bloqueou o envio de um screenshot real para inspeção, por possível exposição de dados internos. Nenhuma imagem foi enviada nessa tentativa. A correção de layout foi verificada por geometria DOM sem conteúdo e pela navegação funcional. Screenshots foram salvos localmente pelo teste; não declarar inspeção visual manual remota dessas imagens.
- API/worker na imagem `midas-data-app:dashboard-all-v1-20260911`, runtime `sha256:58998b6093f92940e53b357dd45d15333824b764de91317246bd210db87bd5ab`. Nove arquivos de implementação/interface conferidos por hash entre host/API/worker. Seis serviços saudáveis, migrations 001–019; somente API/worker foram recriados. Seis ledgers paper idênticos antes/depois, zero regras/bindings/posições ativas. Coletor e outros executores mantiveram imagem/início.

## O que continua pendente

As estratégias anteriores permanecem em catálogo: a adaptação de cadeia/features/idade, calibração completa e motor de parciais ainda é o recorte descrito em [LEGACY_STRATEGIES.md](LEGACY_STRATEGIES.md). Esta ampliação de dashboard não as ativa. Traders, social e outras cadeias sem coleta homologada não aparecem como dados disponíveis. A pesquisa continua limitada pela cobertura/saídas conhecidas; tabelas de ganhos históricos ou exploratórios não equivalem a validação prospectiva.

Próximo passo funcional: adaptar um perfil completo de estratégia com dados/semântica homologados, replay temporal e evidência no comparador. Não elevar quota ou concorrência de consultas pesadas para preencher a interface.

Evidências e reprodução: `.scratch/dashboard-all-20260911/` contém fontes do harnês, JUnit, `checks-summary.json`, `browser-acceptance.json`, `layout-audit.json`, snapshots antes/depois, `runtime-acceptance.json`, hashes, download bruto e screenshots locais. Backup privado do env anterior em `rollback-reference.json`; versão anterior do app: `legacy-strategies-v1-20260911`. Artefatos anteriores de pesquisa permanecem preservados.
