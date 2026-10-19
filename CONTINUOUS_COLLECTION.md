> Atualização CP-DATA-SPEED-13: a meta 3/5 foi testada e não homologada; perfil mantido 8/16. [Estado atual, ensaios e limitações](SPEED_3S_5S_RESULTS.md). Este documento conserva evidências históricas.

# Agenda contínua GMGN — CP-DATA-CONTINUOUS-11

> **Atualização CP-DATA-SPEED-12:** [otimização implantada e medida](SPEED_RESULTS.md). Perfil ativo 8 s / 16 s, painel 2 s; teste curto aprovado, histórico ~26 s. 5 s / 10 s ainda acumula fila. Três capturas NUL em quarentena; cobertura ampla continua aberta. Os valores anteriores abaixo são históricos.

11/09/2026. Implantação na base local `midas-data-base`, porta 8791. Escopo desta fatia: retomada da fonte, universo sem teto artificial, continuidade da agenda, quota compartilhada e cobertura visível. **A etapa ampla de dados continua aberta; isto não é homologação de todas as famílias nem de estratégias.**

## Resultado observado

Em 2026-09-11T19:14:56.461406+00:00, havia **58.915 tokens SOL cadastrados e 58.915 na agenda**, contra 10 ativos antes. O cursor alcançou o fim do cadastro disponível e continua buscando novas identidades. Isso não significa censo completo da cadeia: a descoberta continua sujeita às listas e à cobertura da GMGN.

A fonte estava atual, com as cinco rotas respondendo. Info individual recebido e aceito dentro da frequência de cada token: **473**; sem primeira consulta individual: **58.099**. Aceite de raw não significa normalização válida: a resposta com NUL descrita abaixo foi preservada, mas bloqueada. O painel mantém essa distinção; `coverage_current` continua falso enquanto há lacunas.

## Mudanças implementadas

- Sem teto de 10 ativos/20 esperando, sem corte top 10 para admissão e sem expiração de acompanhamento após 30 minutos. Identidades antigas permanecem na agenda.
- Descoberta primária por Trenches: `new_creation`, `near_completion`, `completed`; Trending top 100/5m por swaps complementa atividade e redescoberta. Categorias e a resposta completa continuam nos brutos.
- Caixa persistente de candidatos em SQLite: um snapshot posterior não apaga candidatos de outro endpoint. O coletor busca a identidade homologada em lote de até 100 pela nova API `/v1/tokens/lookup`.
- Cadastro anterior importado em páginas de 200, com cursor e admissões na mesma transação. Horário de importação não é apresentado como observação recente da fonte. O evento de admissão conserva identidade, captura e relógio.
- Prioridade temporal: três oportunidades para tokens vistos recentemente, depois uma para o mais antigo devido sem essa restrição. Turno persistido; se a classe recente está vazia, o orçamento vai ao restante. Paper conserva o contrato de demanda existente, sem acionar operações.
- Info aceito cria jobs persistentes de holders e OHLCV, sem teto populacional. OHLCV retoma o último intervalo solicitado, com sobreposição de um minuto e janelas máximas de um dia. Isso tenta cobrir o intervalo desde o checkpoint; uma resposta vazia/parcial da fonte não prova que todas as barras existam.
- 401/403: uma consulta de recuperação após 60 s; falhas seguidas escalam para 5, 15 e 60 minutos. 429 respeita o reset fornecido. Diagnóstico inclui status, formato, tamanho/hash do corpo e tempo, sem guardar credenciais no log.
- Destinos agora servem o lote elegível mais antigo: novas entradas não bloqueiam retries vencidos. O backoff permanece aplicado.
- Painel separa processo, fonte, cadastro, primeiras consultas, atrasos, famílias e quota. A lista de 20 linhas é somente uma amostra de exibição.
- Aumento da coleta revelou o erro ClickHouse 307 (`TOO_MANY_BYTES`) na verificação global de identidades. Consultas passam de 256 para 64 IDs e subdividem apenas esse erro até uma identidade; erro não resolvido segue bloqueando o lote. Limites de leitura/memória, comparação de hashes e detecção de conflitos permanecem ativos.

## Frequência e capacidade

| Componente | Configuração | Significado |
|---|---|---|
| Universo | Sem teto fixo de tokens | Limitado por armazenamento e cobertura da fonte; não por top N local |
| Base | 1 peso/s, um trabalhador HTTP | Orçamento local compartilhado com o limitador Redis; não é a quota contratual da conta |
| Limitador compartilhado | 8 pesos/s neste cliente | Coordenação com os clientes que usam a mesma instância/chave Redis; não prova que clientes externos participem |
| Trenches | Alvo 25 s | Snapshot em lote, sujeito à latência/quota |
| Trending | Alvo 60 s | Top 100 da janela selecionada |
| Info recente / fora das listas | Alvos 10 min / 6 h | Metas de elegibilidade, não garantias de execução nesse prazo |
| OHLCV / holders recentes | Alvos 15 / 30 min | Enriquecimento individual após aceite de info |
| OHLCV / holders antigos | Alvo 6 h | A fila e as lacunas permanecem visíveis |
| Agenda e status | SQLite e agregações SQL; até 20 itens por resumo | Sem carregar o universo inteiro em cada heartbeat |

No teste inicial com 2 pesos/s, a janela real de 330.704 s teve fonte atual nos 12 snapshots e nenhuma nova falha HTTP da GMGN. Foram 19 capturas de Trenches, 10 de Trending, 163 de info, 64 de OHLCV e 15 de holders. As médias observadas entre capturas foram aproximadamente 17,4 s, 33,1 s, 2,03 s, 5,17 s e 22,05 s, respectivamente. Não confundir intervalo entre consultas de um endpoint com frequência de cada token.

**O perfil inicial de 2 pesos/s acumulou fila no ClickHouse deste host.** A configuração final foi ajustada para 1 peso/s, Trenches a cada 25 s e Trending a cada 60 s, sem reduzir o universo. No perfil final a fila começou a diminuir; não foi esvaziada neste aceite. Ver `final-profile-watch.json`.

**A cobertura individual de 58 mil tokens não fica instantânea.** Mesmo uma passagem ideal sem novas entradas, retries ou repetição dos recentes teria duração aproximada `N × intervalo observado`: mais de um dia para info e muitos dias para holders neste orçamento. Como a prioridade serve os recentes, o restante pode demorar ainda mais. A meta de seis horas para todos não foi atendida nem prometida. O caminho para expandir cobertura útil é aproveitar os snapshots em lote e ajustar a frequência por família/capacidade; elevar quota só depois de verificar orçamento compartilhado e destinos. Não reintroduzir um teto de entidades para esconder atraso.

## Validação

| Verificação | Evidência |
|---|---|
| Agenda offline real | 10.000 identidades sintéticas em SQLite; 1.000 claims únicos; reinício conserva 10.000; integridade OK |
| Custo offline | Inserção 0,507 s; 1.000 claim/completion 1,309 s; snapshot de 20 itens 2,24–3,73 ms. Não é benchmark da GMGN nem memória total do processo |
| Fonte/schedule/regressões | 82 passaram; 9 integrações Redis desabilitadas nessa suíte |
| Redis real isolado | 11 unittest passaram em namespace de teste; dois se sobrepõem à suíte anterior e nove ativam integrações antes ignoradas |
| Pacote da base | 1022 execuções passaram; 227 integrações não habilitadas. Não somar reexecuções ou casos importados como testes únicos |
| Destinos/worker após correção | 25 passaram, incluindo reprodução SQL da exclusão das tentativas vencidas e teste da ordem corrigida |
| Destinos/worker após correção | 25 passaram, incluindo regressão SQL de prioridade de retries, backoff e guard de identidades |
| API/SQL | 13 passaram em schema PostgreSQL isolado: namespace, corte temporal, lote, duplicatas e limites |
| Navegador | 13 verificações Chromium headless reais, desktop/mobile, DOM, API local e sem chave; nenhum erro JS/HTTP 5xx ou escrita. A ferramenta CUA não iniciou por ACL; foi usado Playwright local |
| Brutos | 8 hashes/downloads verificados, três anteriores e cinco novos; todos os oito concluíram normalização |
| Reinício em produção | 58.183 identidades anteriores preservadas; 58.231 após retomada; timestamps aceitos monotônicos; checkpoints de famílias e cursor preservados |
| Proveniência | Amostra de admissões ligada a capturas reais de Trenches e Trending; 36 tokens fora da prioridade recente já consultados no snapshot intermediário |
| Paper | Contagens dos seis ledgers iguais antes/depois; não é comparação de hashes de todas as linhas |
| ClickHouse | Sonda somente leitura: 256 IDs leram 120.936.318 bytes; quatro consultas de 64 leram individualmente 32,4–34,3 MB. Regressões comprovam visita a todos os IDs, detecção de conflito no último grupo e propagação de erros não divisíveis |

## Falhas encontradas e estado

1. **Migração inicial falhou:** identidade persistida v1 foi rejeitada pela v2. A migração agora aceita somente a versão anterior na mesma fonte/cadeia; teste e reinício real passaram. Não houve descarte do spool.
2. **401 anterior:** a consulta de recuperação de Trenches teve sucesso; a origem remota daquele 401 não foi determinada. Houve outro 401 real em token_info às 18:56:33 UTC; o coletor retomou após 60,5 s, sem intervenção manual. Esse erro posterior não pertence à janela inicial de 330,7 s sem erros. A nova política foi exercitada também por falhas simuladas, distinguindo-as da recuperação real.
3. **Uma quarentena real:** `953e8e36-8b70-4599-b36b-e2a642386584`, SHA-256 `193ecbb1ecb0c9012e28d2fc69c64de12b1e41e2917fe6c0714b7e0ea6062449`. A GMGN enviou NUL em `/data/pool/quote_symbol` (`ANTFUN` seguido de quatro NUL). O bruto permanece íntegro. O contrato atual recusa NUL e bloqueia a normalização da captura. Tratamento localizado desse campo e replay com versão explícita pertencem a D3; não remover bytes do original nem afirmar que já são dados elegíveis.
4. **ClickHouse 307 e prioridade de retries:** a carga maior expôs leitura excessiva no guard de identidades. Corrigida a subdivisão preservando os limites; histórico de falhas permanece como histórico. A fila antiga também priorizava novas entradas: reproduzida e corrigida a ordenação. Os 43 lotes em erro foram retomados e os erros pendentes zeraram; ainda havia backlog a entregar. Estado em `final-profile-watch.json` e `verification.json`.
5. Sem prova de operação sustentada por dias, cobertura temporal completa ou restauração integral da base. A cópia/retomada do SQLite não fecha D4/D5.

## Operação e reversibilidade

API/worker e coletor usam as tags `continuous-v2-20260911`; os IDs exatos estão em `verification.json`. Configuração persistente em `.data/gmgn-data/base-runtime.env`. Containers de Postgres, ClickHouse e NATS não foram recriados. Executores e outros projetos não foram administrados nesta fatia.

Há backup consistente pré-migração do SQLite em `.scratch/gmgn-continuous-20260911/spool-before.sqlite3`, conferido por SHA-256 e `integrity_check`. A configuração anterior fica no diretório privado `.data/gmgn-data/`. Não trocar apenas a imagem para v1 sobre a agenda v2: a política antiga pode expirar registros. Uma reversão exige parar o owner, preservar o estado novo e reconciliar as capturas/progresso posteriores antes de restaurar uma cópia. Nenhuma limpeza ou restauração destrutiva foi executada.

## Próximo passo real

D2 tem a base de universo/agenda implantada e retomada comprovada; continuidade por dias e todas as transições de cobertura ainda precisam de aceite. D1/D3: completar matriz de famílias e tratar campos problemáticos com interpretação explícita, começando pelo NUL observado, depois integrar famílias dedicadas faltantes sem duplicar quota/raw. D4: backup/restauração integral atual. D5: frequência concedida por família, capacidade sustentada e crescimento dos destinos. Estratégias continuam adiadas.

Fontes técnicas: [limites de complexidade do ClickHouse](https://clickhouse.com/docs/concepts/features/configuration/settings/query-complexity) e [índices para descartar blocos](https://clickhouse.com/docs/concepts/best-practices/using-data-skipping-indices). A explicação de ampliação das leituras por múltiplas sondas Bloom é inferência apoiada pelo índice real e pela sonda; as medições acima são locais.

Evidências: [verification.json](../outputs/gmgn-continuous-20260911/verification.json), [manifesto](../outputs/gmgn-continuous-20260911/manifest.json).
