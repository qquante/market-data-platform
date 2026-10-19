# Base corrigida e inventariada — CP-BASE-READY-07

> Atualização 11/09/2026: [dicionário operacional CP-BASE-FIELDS-08](FIELD_DICTIONARY.md) instalado; 831 caminhos das cinco fontes e cobertura por ocorrência. As contagens e pendências abaixo pertencem ao checkpoint histórico indicado.

11/09/2026. Pedido vigente: **estratégias ficam para depois; estabilizar a base e dizer todos os dados coletados**. Trabalho restrito a `midas-data-base`, sem aumentar quota, alterar regras/posições, criar automações ou desligar PONS/Telegram.

## Correção entregue

`token_kline` era enviado ao normalizador de fatos de token `gmgn-p0-v4`, que não tinha adaptador para esse endpoint. O leitor `gmgn-candles-read-v1` conseguia ler o mesmo bruto; a captura ficava indevidamente classificada como endpoint não suportado.

A admissão agora usa esse mesmo leitor, com interpretação **gmgn-candles-normalize-v1**. Time/OHLC/volume são classificados por linha; campos adicionais continuam preservados. Não se criam fatos de token a partir de barras retroativas. Schema, dados, resultados e IDs v4 dos outros endpoints permanecem iguais.

Foram reclassificadas **258 capturas** com manifesto prévio (256 + 2 recebidas na transição). Original, SHA-256, publicação original e runs v4 foram preservados; a nova interpretação recebeu publicação atual. As 150 ocorrências de barras fora do intervalo do primeiro manifesto permanecem indisponíveis. Captura pronta significa interpretada, não cobertura de 100% das barras nem qualidade válida de todo campo.

Após a correção: seis serviços saudáveis, 8.130 capturas GMGN prontas no snapshot final, nenhuma GMGN em quarentena nesse instante, processamento corrente e seis ledgers paper idênticos. Novas capturas são processadas pelo adaptador instalado; contagens crescem com a coleta.

## Inventário completo

[Inventário dos dados coletados](INVENTARIO_DADOS_COLETADOS.md): cinco endpoints, parâmetros reais, cadência, todas as categorias e **795 caminhos de campos em 25 capturas reais**. Inclui nulos, campos desconhecidos e contratos de interpretação. JSON exportado em `outputs/gmgn-base-inventory-20260911/field-inventory.json`.

Trenches: apesar do pedido de 2 itens/categoria, as cinco respostas auditadas continham 60 em cada uma das três categorias. Não há truncamento do bruto para aparentar respeito ao pedido. Top holders pede/observa 100 carteiras; são listas parciais. Inventário por amostra recente não garante todos os campos opcionais de todo o histórico.

## Validação

- Dez regressões reproduziram o problema antes da correção; passaram depois.
- **942 testes únicos aprovados**, contando as identidades dos casos nos XML empacotado + SQL, sem somar contadores auxiliares ou casos repetidos. 33 skips no pacote não contam como aprovações; três casos SQL únicos adicionais foram executados em schema privado.
- SQL: versões antiga/nova coexistem; mesmo bruto; publicação antiga preservada; novo corte; zero fatos artificiais; repetição idempotente; nenhuma mudança de estado ao repetir o run antigo.
- Navegador real: 16 fluxos, seis áreas, dados/holders/candles, paginação/download, zero erros JS/5xx e zero Authorization em consultas. Nenhuma estratégia ou experimento novo foi criado por esses checks.
- API/worker atualizados; coletor e todos os outros containers preservaram imagem/início. Ledgers, regras, bindings e posições preservados.
- A primeira tentativa de suíte host incluía worker e não coletou por ausência local de `nats`. O gate completo foi executado na imagem empacotada com as dependências reais e passou. Não foi instalado pacote adicional no host.

Imagem: `midas-data-app:base-readiness-v1-20260911`. Código de reparo: `scripts/gmgn-reconcile-candles.py`, modo padrão somente plano; aplicação exige manifesto e `--apply`. Evidências `.scratch/base-readiness-20260911/`: XML, before/final, manifestos/resultados, identidade dos containers, inventário e browser. Configuração privada anterior foi guardada para rollback; a correção não exige rollback de schema.

## Estado do plano e limites mantidos

- TASKS, GOALS, EXECUTION_MAP e SESSIONS atualizados. A tabela numerada já não anuncia publicação de holders/UI como pendente de autorização. O documento de planejamento de 08/09 está identificado como referência histórica.
- Estratégias, adaptação do catálogo antigo e validação científica adiadas por pedido atual.
- Metas de coleta preservadas: 10 ativos / 20 esperando, 600 s por token, cinco endpoints com vagas a cada 60 s, quota 1 peso/s. Não é coleta integral nem instantânea.
- Traders/outras cadeias/corpus social dedicados permanecem expansões, não correções de dados já homologados. Dados embutidos atuais estão no inventário.
- Retenção de longo prazo e capacidade sustentada ainda não certificadas. Não foi criado purge nem apagado histórico. Os ensaios anteriores e seus limites continuam em RESEARCH_QUALIFICATION.md.
- Backup/restore completo atual não foi executado. O histórico de 09/09 registra que o usuário dispensou um novo ciclo após o reset; o script antigo fixa o projeto `midas-data` e não deve ser usado diretamente como se validasse `midas-data-base`. Sua adaptação/ensaio precisa de recorte próprio. A cópia privada de configuração desta entrega não é backup dos dados.
- PONS/Telegram são frentes separadas preservadas; não são consumidores migrados por este aceite. Automação de hora em hora continua removida.
