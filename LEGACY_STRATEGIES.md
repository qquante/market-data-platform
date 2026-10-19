# Estratégias anteriores — catálogo importado

Checkpoint CP-LEGACY-STRATEGIES-04. Implantado em 11/09/2026 na base local, porta 8791.

O painel **Pesquisa → Estratégias anteriores** agora permite buscar por nome, critério, par ou run; filtrar seleção e primeiro alvo; paginar; inspecionar entrada/saídas; baixar uma definição ou o catálogo completo com SHA-256 conferido no navegador.

## O que foi recuperado

| Fonte local | Definições | Calibração |
|---|---:|---|
| Parciais selecionadas, snapshot 07/09 22:04 UTC, run `eb3c9c65-87f0-43cc-a6fc-3b076b8ec704` | 277 | Médias, desvios, transformações, pesos e corte preservados; 46 entradas e 55 saídas |
| Candidatas de parciais rápidas, snapshot 07/09 23:13 UTC, run `82d35592-109f-4723-a499-e61daa09d12b` | 41 | O arquivo tem direções/pesos/corte, mas omite a calibração completa |
| Perfil parcial 70%, run `b0f80119-324a-4df7-99aa-7f9eb5193cc3`, ativação histórica 08/09 00:45 UTC | 1 | Perfil completo; aprovação shadow pertence ao legado |

São **319 definições de Robinhood**, de seleções anteriores, sem reranqueamento pelos ganhos antigos. A seleção de 277 preserva os grupos de primeiro alvo: +66% (25), +75% (19), +100% (34), +125% (27), +150% (74), +200% (35), +300% (63).

A parcial 70% mantém: vende 70% da posição inicial em +100%, 10% em +500%, 10% em +900% e 10% em +1200%; SL -40% sobre o saldo restante; horizonte 6h. O run original usa corte `0.18589948667035508`, com médias/desvios ausentes no resumo. A recalibração shadow usa `0.15469285070471994`. Os dois registros têm IDs distintos; não foi preenchido o original com valores do run posterior.

Os arquivos originais foram copiados byte a byte para `midas_data/strategy_sources/`. O manifest registra caminho de origem e hash; qualquer divergência no conteúdo recusa a leitura. A identidade inclui run, cadeia, horizonte, idade, par, regra, calibração e saídas. Ausência de calibração permanece ausência. As métricas antigas só aparecem no detalhe como simulação histórica e carregam o snapshot.

O catálogo não importa automaticamente todos os candidatos gerados em todos os runs. `SCORES_SOL_2026-09-07.json` contém 8.760 combinações resumidas de Solana, fora dessas seleções; não foi convertido em estratégias executáveis. O endpoint antigo na porta 8787 estava indisponível na investigação, portanto as fontes desta entrega são os arquivos preservados.

## Estado e próximo passo real

**Importação concluída; execução/adaptação ao comparador novo pendente.** Todos os registros têm `runtime_status=catalog_only`, `compatibility.state=blocked` e `strategy_validated_in_current_base=false`.

A pesquisa atual aceita Solana e entrada por limites simples de market cap/holders, com saída em horizonte fixo. As estratégias antigas exigem Robinhood, score ponderado calibrado e saídas parciais. Não foram alterados `ExperimentSpec`, o motor de simulação, as regras ativas ou as bindings paper para simular compatibilidade.

Para reproduzir essas estratégias de fato, o próximo recorte é adaptar um perfil completo (a parcial 70% é o perfil preservado em destaque): homologar cadeia/features e clocks, mapear idade do token versus inscrição, implementar score com ausência rejeitada e o contrato de parciais/SL/saída observável. Depois, executar replay temporal com cobertura e custos explícitos. As 41 definições incompletas precisam recuperar a calibração original ou criar uma nova versão declarada. Ainda não há estratégia validada nesta base.

## Contrato e operação

Rotas GET autenticadas:

- `/v1/research/strategies?offset=0&limit=20&source=selected_partials&first_target=66&query=...`
- `/v1/research/strategies/{strategy_id}`
- `/v1/research/strategies/export`
- `/v1/research/strategies/{strategy_id}/export`

Limite de página 100; consulta textual até 100 caracteres. O catálogo é carregado uma vez por processo a partir dos arquivos da imagem; não consulta GMGN, SQL ou o legado. Não adiciona coleta, quota, migração, cron ou serviço. O export completo tem 997.501 bytes e SHA-256 `f8d30293233382629b02c2e76a4a4375cb442979c38bf5bd3c3ec1521bba77b5`.

API/worker: `midas-data-app:legacy-strategies-v1-20260911`, identidade runtime `sha256:286c5417eb6d66bb8e48c4624abc81916e73fe6bb4c8ec6a56dee1e280f29123`. Coletor preservado em `research-candles-v1-20260911`. Só API/worker foram recriados; outros serviços e executores mantiveram imagem/início. Backup do env anterior permanece privado, referenciado em `.scratch/legacy-strategies-20260911/rollback-reference.json`.

## Validação executada

- 9 testes locais passaram, incluindo seis regressões novas: importação exata de todas as 277 linhas, conservação das frações, duas calibrações do mesmo par, integridade do perfil/fonte, paginação/export, autenticação e recusa de POST sem acessar banco.
- Imagem empacotada: **935 testes aprovados, zero falhas/erros, 33 skips**. Os skips não contam como testes aprovados. Integrações SQL e scripts de backup host não foram executados neste recorte, que não muda persistência. Após essa suíte, apenas a largura mínima das tabelas foi ajustada para leitura mobile e revalidada no navegador; código Python permaneceu idêntico.
- Browser real 1440×1050 e 390×844: 319 definições, filtros/páginas, oito features/quatro parciais do perfil, calibração incompleta visível, downloads com hash, endpoints privados 401, zero erros JavaScript e zero overflow da página. Tabelas largas usam rolagem própria. Capturas inspecionadas visualmente.
- Runtime final: seis serviços saudáveis, migrations 001–019, nove fontes/arquivos do recurso com hashes iguais entre host/API/worker. Seis ledgers paper iguais antes/depois; zero regras, bindings e posições ativas na base nova.
- O primeiro verificador de containers detectou também o desaparecimento do container temporário da própria suíte de testes. A conferência final o identificou pelo hash da imagem; os serviços alheios permaneceram inalterados.

Evidências: `.scratch/legacy-strategies-20260911/` contém JUnit, aceites HTTP/browser/runtime, snapshots antes/depois, screenshots, catálogo baixado e cópia das fontes desta entrega. O protocolo e os resultados de pesquisa do checkpoint anterior não foram recalculados nem reclassificados por esta importação.
