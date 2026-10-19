# Base Docker — fechamento operacional, 09/09/2026

> Atualização posterior: v4 implantada e verificada em base limpa, cinco serviços saudáveis e três smokes aprovados. [Checkpoint atual](CHECKPOINT_V4_20260909.md). A descrição anterior abaixo preserva o histórico pré-implantação; coleta real continua pendente.

Owner: A0. Escopo: infraestrutura existente; nenhum framework, token, estratégia, interface ou nova chamada à GMGN. Backup/restore e migração histórica foram retirados pelo usuário. Volumes antigos preservados para eventual descarte posterior.

## Runtime

Projeto `midas-data-base`; API local `http://127.0.0.1:8791`; configuração privada `.data/gmgn-data/base-runtime.env`. O wrapper `scripts/gmgn-data.ps1` usa esse projeto por padrão. `-Project midas-data` seleciona explicitamente o ambiente anterior. Não iniciar ambos na mesma porta. PostgreSQL, NATS, ClickHouse, API e worker ativos; ponte não iniciada porque o coletor legado foi encontrado parado. Nenhum executor legado foi iniciado.

PostgreSQL mantém bruto/manifesto/outbox/publicação/recibos; DuckLake mantém Parquet e snapshots; ClickHouse mantém projeção verificada. A migration 007 adiciona a fila e confirmação por destino. O inicializador conserva recibos legados válidos; a implantação atual usa volumes novos e não migrou histórico.

## Mudanças e diagnóstico

- Relay NATS, normalização, Lake, ClickHouse, manutenção e heartbeat executam em ciclos independentes dentro de um worker. Uma sessão proprietária e um lock por sink preservam escrita exclusiva. A manutenção só disputa a escrita do Lake.
- Cada destino possui recibo, tentativas, próxima tentativa e erro. A confirmação completa exige os dois recibos iguais. Um destino indisponível não repete a escrita já confirmada do outro.
- A captura inicia com PostgreSQL e armazenamento, sem depender da saúde dos destinos. `/healthz` informa processo/banco; `/v1/status.pipeline` distingue `accepting`, `worker_live`, `processing_ready` e `processing_current`, com filas/idades/erros/último progresso.
- O ClickHouse retornou código 307 (`TOO_MANY_BYTES`): uma consulta global de identidades ultrapassou 128 MiB lidos. O teste com transporte multipart reproduziu o mesmo erro. A consulta passou a usar grupos de até 256 identidades, mantendo checksums, precisão e orçamento por consulta.
- Timeout transitório reagenda o grupo; não multiplica imediatamente oito tentativas individuais. Isolamento por item fica reservado a conflitos/contratos inválidos. Inicialização parcial do consumidor NATS pode ser repetida sem deixar o worker preso sem consumidor.
- Permanecem até oito capturas/grupo, 10 mil fatos e 16 MiB por etapa; normalização também limita grupos a 10 mil observações de campos. Uma captura indivisível conserva seu contrato. Proteção por espaço físico, reserva mínima de 4 GiB, compactação limitada e recursos do Compose preservados. Não existe limite artificial de 500 capturas pendentes.

## Evidência

Diretório privado: `.data/gmgn-data/evidence/closure-20260909`.

- `read-repro.json`, `transport-comparison.json`: erro reproduzido em 6.580 fatos reais; não confundir erro de consulta com limite de URL. `identity-regression.json`: os mesmos 6.580 fatos e o ClickHouse histórico passaram com a correção, 26 consultas em 2,025 s, mantendo 128 MiB por consulta. A verificação apenas leu o ambiente anterior; seu ClickHouse foi novamente parado.
- `sql.xml`: três provas PostgreSQL reais; migration, confirmação independente, replay, atomicidade e inicialização compatível.
- `failures.json`: captura e Lake progridem com ClickHouse parado; ClickHouse progride com catálogo Lake indisponível; captura persiste com NATS parado; recuperação preserva recibos; worker reinicia sem alterar efeitos; bruto e duplicata conferem.
- `integrated.xml`: suíte consolidada da versão final, 453 casos aprovados e 28 opt-ins/PowerShell ignorados (o total agregado do XML inclui subverificações), zero falhas (29,938 s). Cinco testes reais de sinks agrupados passaram com worker parado, registrados em `integrated-interrupted.xml` antes da interrupção de outro teste. A primeira tentativa de suíte foi interrompida por recriação da API durante o teste da ponte; esse erro de execução do verificador não é um resultado aprovado.
- `message-recovery.xml`: PostgreSQL e NATS reais, schema/stream privados; ACK de publicação perdido após aceitação real pelo servidor, mensagem expirada, republicação e redelivery sem repetir efeito. TTL acelerado de dois segundos e stream de teste em memória; não simula persistência em disco nem a passagem real de 24 horas. Persistência/reinício do stream operacional em disco está em `failures.json`.
- `corpus.json`: 128 corpos existentes, 10.656.545 bytes. Replay rotulado `fixture`, sem nova coleta; horários econômicos do corpo permanecem originais. A taxa de 226 capturas/minuto é o máximo admitido observado em 31 minutos da base anterior, não a capacidade máxima do provedor.
- `load-*.json*`: observação final a 226 capturas/minuto por 60 minutos. Até seu encerramento e análise, capacidade sustentada permanece em validação. `warmup-*` é apenas aquecimento anterior à correção final do broker.

## Limites e próximo passo

O `TimeoutError` antigo da normalização não foi reproduzido isoladamente; o teste sustentado deve mostrar se reaparece sob a carga medida. Não há garantia de capacidade ilimitada ou de ausência de gargalos futuros. Espaço de host é compartilhado com outros projetos; crescimento de bancos/catálogo e espaço livre devem ser registrados ao encerrar.

A origem legada está parada. A base pode ser testada integralmente com replay, mas atraso origem→captura de 60 segundos e cobertura de coleta viva não podem ser certificados nessa condição. Token e homologação adicional ficam para depois. Uma futura conexão à origem deve usar o checkpoint/credencial próprios do novo projeto; não copiar uma posição antiga para pular evidência.


09/09/2026 — Usuário dispensou a janela de 60 minutos e pediu somente status. Replay interrompido conforme solicitado: 3677 capturas aceitas em 16.27 minutos, zero erros de ingresso/etapas observados. Resultado load-result.json; coleta viva e observação longa não certificadas. Nenhuma alteração adicional de código. Dados antigos preservados.

09/09/2026 — Atualização posterior: o usuário assumiu reset manual após bloqueio automático. Todos os containers Midas encerrados. O runtime descrito acima não está ativo; nova versão de contratos testada, sem implantação. Ver TRAJECTORY_CONTRACTS.md e MANUAL_RESET.md.
