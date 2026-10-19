# Entrega GMGN 6.1–7.4 — aceite operacional v1

> Estado CP-BASE-READY-07: [correção dos candles e limites atuais](BASE_READINESS.md); [inventário completo dos dados coletados](INVENTARIO_DADOS_COLETADOS.md). Estratégias adiadas pelo usuário.

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

CP-RESEARCH-UI-01, 11/09/2026. Owner A0, execução local sem subagentes. O usuário autorizou implementar, pesquisar, testar e documentar 6.1–7.4 e continuar nesta task a cada hora. Evitar overengineering permanece uma restrição: uma fila existente, PostgreSQL, módulos pequenos e UI estática; nenhum novo serviço permanente.

**7.4 concluída no escopo operacional v1 em 11/09/2026.** Após o pedido “finalzie entao”, API/worker/coletor foram implantados, holders contínuo foi habilitado dentro da quota e os consumidores foram homologados na base real. Relatório vigente: [LIVE_ACCEPTANCE.md](LIVE_ACCEPTANCE.md). A continuação horária permanece removida.

## Estado por etapa

| Etapa | Entregue e comprovado | Limite / trabalho posterior |
|---|---|---|
| 6.1 | Holders HTTP v2, persistência, agenda e coleta real: 11 capturas / 10 tokens / 1.100 linhas, retomada e hashes conferidos | Famílias adicionais precisam de contratos próprios |
| 6.2 | Universo/coortes, negativos/labels/cobertura e checkpoint; jobs reais de 17 e 98 membros concluídos | Amostra de homologação sem cobertura econômica suficiente não valida hipótese |
| 6.3 | Simulação com custos/splits/purge e estudos descritivos implantados | Dados reais do ensaio permaneceram unknown; sem rentabilidade/fills ou inferência certificada |
| 6.4 | Comparação real de dois cenários/três estudos, tentativa idempotente, exportação e replay byte a byte | Amostra madura e protocolo científico são trabalho posterior |
| 7.1 | Painel operacional publicado em 8791; saúde/frescor/recursos/filas conferidos | Observação contínua pelo operador |
| 7.2 | Trajetórias/proveniência/holders/decisões paper no painel real desktop/mobile | Limites de cobertura aparecem como ausência/gaps |
| 7.3 | Carga isolada, orçamento, retenção e retomada testados; pequena janela real aprovada | Não certifica capacidade sustentada ou concorrência ilimitada |
| 7.4 | **Concluída v1:** migrations 016–018, seis serviços saudáveis, canário/retomada/consumidores/replay/docs e ledgers preservados | Sem pendência de implantação nesta entrega |

Q05–Q10 possuem implementações v1 descritivas documentadas, não todos os estimadores/garantias avançadas dos contratos propostos de 08/09. Q14 exige corpus social versionado; traders/OHLCV/outras cadeias exigem contratos próprios. Não foram inventados esses dados ou declaradas essas expansões como concluídas. Ver [RESEARCH.md](RESEARCH.md).

## Implementação do checkpoint local (histórico)

- midas_data/holders.py, holder_store.py e migration 016: parser HTTP, identidade por carteira, atomização da publicação, consulta temporal e limites de página.
- src/midas_gmgn/family_schedule.py e data_collector.py: enriquecimento opt-in de tokens admitidos, mesma quota/SQLite, falhas/ACK/reinício e heartbeat limitado; Compose/Docker incluem o módulo, mas holders permanece desligado na configuração real.
- research_contracts/cohorts/research_jobs e migration 017: universo, labels, artefatos e fila retomável.
- experiments/studies/experiment_store/research_api e migration 018: custos/splits, estudos, comparação, retenção e reprodução.
- resources/dashboard/ui e integrações api/worker: painel operacional, trajetórias, decisões e experimentos.
- Testes unitários/SQL, harnês de prévia, navegador, carga e geração/reprodução sintética.

Mudanças preexistentes das frentes Telegram/PONS e outros arquivos da árvore suja foram preservadas. Não houve commit/merge/deploy geral.

## Evidência do checkpoint local anterior à implantação

- Imagem final: midas-data-app:research-ui-v1-20260911, ID Docker SHA-256 72082e1c41acbd6e1008bfda4c45169dbac6f88b6a223b073acbcf3e5114a05f (config dff8dd659fa27645b16b61c3a270fea6aca57f07310b2658cf7297149a0d430c).
- Coletor final: midas-data-collector:research-schedule-v1-20260911, ID Docker SHA-256 ebdb97f948d32f6388899245ea9c42930961c8ad3f2c3bbc68f83a115938d69c (config 2422199e7bdd6952eea7735778d20d7d15225d5ea6f081e0dbaa78969bffaf5f).
- Testes finais da imagem, integração privada e coletor registrados em .scratch/checkpoint-final-20260911. Consulte acceptance-summary.json para contagens/hash/código exatos.
- Browser contra imagem final: 29 pontos/3 segmentos, exportação conferida por SHA-256, zero erros de página e sem overflow mobile.
- HTTP 100 concorrentes: holders 200/200 aceitas, p95 604,54 ms; trajetórias 2/200 aceitas e 198 recusas 429 esperadas. Recuperação serial aprovada. [Carga](LOAD_ACCEPTANCE.md).
- Reprodução offline: cem tokens sintéticos, vinte estratégias/quatro estudos; resultado idêntico, inclusive via imagem instalada.
- Uma chamada GMGN read-only em 03:01:25Z: dez holders, 902 campos, zero issues. Hash/raw/metadados em .scratch/checkpoint-holders-store-20260910. Não foi ingerida no runtime real.
- Sondagem read-only final em 04:44:28Z: migrations reais 001–015; nova rota de pesquisa 404; ledger paper idêntico ao baseline, zero regras/bindings/posições ativas. Isso comprova ausência de implantação; não é falha da imagem nova. Seis serviços reais anteriores preservados e saudáveis.
- Limpeza: o primeiro shutdown da prévia revelou schemas fixture remanescentes. A causa foi o Uvicorn reemitir SIGTERM antes do finally externo. Limpeza movida para o lifespan; novo start/stop provou exclusão automática. Os três schemas anteriores foram removidos apenas após conferir identidade/conteúdo e criação nesta task. Nenhuma tabela real foi excluída. Provas owned-fixture-cleanup.json e preview-cleanup.json.
- O reempacotamento final alterou somente o harnês; suíte da imagem/23 gates SQL executados novamente. Código do painel/carga permaneceu idêntico. Tree SHA-256 do runtime: b1c075747869390d51890dfa7de3c918186e2a1dd00847a2c098818209be65b8.

Contagem final: **987 testes aprovados** — 905 na imagem instalada, 23 SQL em schemas privados (16 de integração/consumidores + sete bindings) e 59 do coletor. A suíte ampla teve 30 skips: sete SQL foram depois executados com opt-in e aprovados; 23 gates de backup que exigem PowerShell host não foram executados neste recorte. Nenhum skip foi contado como aprovação. A contagem usa os testcase únicos aprovados: 905 + 23 + 59 = 987; os 48 registros auxiliares adicionados ao contador agregado XML da plataforma não foram somados como testes independentes. Falhas iniciais de caminho de montagem/Settings fixture e duas expectativas de fixture foram corrigidas e reexecutadas; não houve falha final ocultada.

## Fechamento da implantação — CP-RESEARCH-UI-LIVE-02

O bloqueio anterior foi resolvido pela resposta explícita do usuário após a explicação do escopo. Implantação e verificações ocorreram somente em midas-data-base. Configuração anterior guardada para rollback operacional sem remoção de dados. O maior atraso de publicação de holders foi 2,868 s e o menor intervalo de requisição 60,008 s; nenhuma falha nova desse endpoint. Filas zeradas e ledgers paper idênticos no aceite. Painel real, oito consumidores HTTP e reprodução offline aprovados. Consulta inicial inválida do harnês recebeu 422 e foi corrigida sem modificar a API.

Pesquisa real de 98 tokens preservou 98 entradas/labels unknown devido à ausência do campo econômico homologado de Trenches; não apresentou resultado financeiro informativo. Isso não é declarado sucesso de estratégia. O recorte operacional terminou, e a qualificação de dados/amostra permanece trabalho posterior. Veja comandos, evidências e limitações em [LIVE_ACCEPTANCE](LIVE_ACCEPTANCE.md).

## Continuação programada

Automação concluir-refatora-o-gmgn-at-7-4 removida em 11/09/2026 a pedido explícito do usuário. Não haverá novas continuações de hora em hora. A remoção da agenda não desliga o coletor Docker. A implantação foi posteriormente concluída conforme CP-RESEARCH-UI-LIVE-02.

Referências: [Holders](HOLDERS_STORAGE.md), [Pesquisa](RESEARCH.md), [Painel](DASHBOARD.md), [Carga](LOAD_ACCEPTANCE.md). BACKUP.md e demais históricos continuam registros de seus checkpoints; esta página prevalece para o estado da entrega 6.1–7.4.

## Histórico: encerramento da continuação horária antes da autorização — 11/09/2026

Em 2026-09-11T10:25:12.051859+00:00, o usuário pediu retirar a recorrência e resumir acertos/falhas. automation_update retornou deleteStatus=deleted. Código de runtime/coletor continua idêntico ao manifesto aprovado localmente; 987 casos únicos passaram nos testes registrados. Conferência atual do Docker: seis serviços saudáveis nas imagens anteriores, sem implantação nova. As execuções horárias anteriores apenas conferiram a pendência e os hashes, sem implementar ou implantar alterações adicionais. 7.4 permanece pendente; nenhum novo pedido de autorização foi enviado nesta revisão.
