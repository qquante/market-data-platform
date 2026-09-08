# Resultado da base GMGN via Docker

Entrega: 2026-09-08. Escopo concluído: fundação local de dados, com controle em seis tarefas e 19 subtarefas. Coordenação A0 e três agentes especializados. Implementação e provas executadas; não apenas especificação.

## O que está funcionando

- API local autenticada de recepção, recuperação do bruto, catálogo e consultas temporais.
- PostgreSQL operacional com três migrations verificadas por checksum.
- NATS JetStream com outbox persistente e retomada de capturas não consumidas.
- Bruto gzip por SHA256, sem reserializar a resposta recebida.
- DuckLake/Parquet com catálogo PostgreSQL e snapshots; ClickHouse reconstruível.
- Recibos por lote: só marcar aplicação concluída quando os dois destinos conferem.
- Reprocessamento versionado, sem sobrescrever evidência nem somar interpretações da mesma captura nas consultas atuais.
- Relógios UTC distintos, precisão decimal preservada, categorias, unknown/missing/invalid/unverified explícitos e orçamento por consulta/lote.
- Comandos de status, importação limitada, reprocessamento, replay, teste de interrupção, falha parcial e backup/restore.

Operação e contratos: [README](README.md). Controle: [TASKS](../TASKS.md), [GOALS](../GOALS.md), [SESSIONS](../SESSIONS.md), [EXECUTION_MAP](../EXECUTION_MAP.md).

## Provas realizadas

| Verificação | Resultado |
|---|---|
| Suíte final no Docker | 234 testes +5 subtests passaram; 11 testes dependentes de PowerShell foram pulados no container Linux. |
| Guardrails de backup no host | Os 11 testes PowerShell passaram, com 10 subtests. |
| Integração real na suíte | 19 testes HTTP +3 de recuperação temporal/replay/versionamento, incluídos nos 234. |
| API e tamanho | Localhost respondeu 200; corpo acima de4MiB recebeu413 e não criou captura (consulta404). |
| Interrupção | Captura aceita/pending antes de SIGKILL da API + restart NATS: bruto igual, um fato/run e recibos iguais. |
| Falha parcial | CH parado: lake commitado, PG ainda pendente e worker degraded; retomada automática gerou um fato por destino, hashes iguais e retry duplicate=true. |
| Conhecimento histórico | Leitor bloqueado de fato na barreira PG; corte antigo preservado; versão posterior aparece só no corte elegível. |
| Importação GMGN real | 10 capturas,41.609 bytes originais,10 hashes verificados; origem somente em leitura. |
| Correção a partir de dado real | Normalizador v2 suporta os três rankings com envelope duplo e corrige o enum launchpad_status de token_info. |
| Versionamento real | 10 runs v1/476 fatos preservados +10 runs v2/696 fatos; todos os lotes conferidos nos dois destinos. As10 capturas estão ready. |
| Backup/restore final | Projeto/volumes novos,121 lotes reconstruídos e conferidos; PG, catálogo, raw e lake com fingerprints iguais. |
| Preservação do legado | 116 arquivos operacionais comparados com o manifesto anterior: zero alterações. |
| Smoke de consultas paralelas | 20 consultas ao mesmo token real, resultados iguais; mediana106,62ms/p95 111,27ms na amostra pequena. Não é benchmark Big Data. |

Imagem aplicada: `midas-data-app:0.1.0`, digest `sha256:572954c171396cb4a15e2c485d11642025bb72e49c8cc10bfb1f2b9a1dc7a066`. Normalizador ativo: `gmgn-p0-v2`; migrations001/002/003. As dependências Python estão no uv.lock; PostgreSQL/NATS/ClickHouse e Python base estão fixados por digest.

## Backup final conferido

Diretório: `.data/gmgn-data/backups/acceptance-v2-20260908`.

- Backup concluído em22:21:23 UTC; restore passou em22:22:38 UTC.
- 109 capturas:99 fixtures de teste e10 reais legadas, sempre identificadas separadamente.
- 97 objetos brutos,50.859 bytes descomprimidos.
- 121 lotes,1.269 fatos históricos no lake e snapshot122.
- Aproximadamente1,77MB em cinco artefatos, com manifesto e hashes.
- Réplicas de ensaio usam projetos `midas-data-restore` e `midas-data-restore-v2`; volumes preservados. A origem continua no projeto `midas-data`.

Os1.269 fatos históricos incluem versões anteriores e fixtures; não representam1.269 observações independentes de mercado. As queries públicas filtram origem e selecionam a interpretação elegível pelo corte.

O backup final conserva somente o universo efetivamente capturado nessa base nova; não é backup dos bancos legados completos. Este é um ensaio local, sem prova de perda do host, réplica externa ou PITR.

## Evidências locais

- [JUnit da versão final](../.data/gmgn-data/evidence/foundation-v2.xml).
- [Importação original](../.data/gmgn-data/import-result-20260908.json) — preview v1 no momento da importação; a correção posterior está nos runs v2.
- [Interrupção API/NATS](../.data/gmgn-data/recovery-2079eb5d-9aea-4ec3-ac25-d9d2523848d3.json).
- [Falha parcial ClickHouse](../.data/gmgn-data/partial-recovery-acceptance-20260908.json).
- [Restore final](../.data/gmgn-data/backups/acceptance-v2-20260908/restore-check-20260908T2222000767210Z.json).
- [Arquivos legados preservados](../.data/gmgn-data/evidence/legacy-files.json).
- [Matriz de aceite](tests/ACCEPTANCE.md).

Esses arquivos de evidência, dados, credenciais e backups ficam fora do Git; os procedimentos e registros de conclusão estão versionados.

## Limites e próximo passo

A base está operacional para o escopo demonstrado. O coletor contínuo legado permanece separado; a integração real desta entrega é uma importação limitada e uma API pronta para receber capturas. Não foi realizada migração integral nem substituição do coletor.

Os696 fatos v2 da amostra incluem33 valid,580 unverified,51 invalid e32 missing. Valid também inclui campos de identidade/enum; não significa33 medidas financeiras homologadas. Preços zero continuam sinalizados, sem transformá-los em preços positivos nem presumir sentinelas.

A próxima etapa de dados é homologar unidade/janela/escala dos campos restantes, ampliar adaptadores e conectar a captura contínua a esta fundação, usando replay e os mesmos gates. Migração histórica, retenção/compactação, backup externo/PITR e carga representativa precisam de etapas próprias antes de alegar prontidão em escala. Estratégias, pesquisa causal/preditiva e UI continuam fora desta entrega.
