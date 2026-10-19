# Acompanhamento GMGN — etapas 3.1 a 3.4

> Atualização 5.3.4.1: [demanda paper](PAPER_TRACKING.md) também alimenta a fila. Mantidos capacidade, quota e frequência; admissão prioriza posições e pode devolver à espera uma descoberta ainda não iniciada, preservando seu prazo. A política abaixo registra a entrega inicial 3.4; a extensão paper está no relatório vinculado.

Implementado em 10/09/2026, A0, sem subagentes. **Concluído até 3.4 e implantado.** A pedido do usuário, 3.1 foi reduzida à checagem mínima para implantar a base; medição sustentada fica para quando o fluxo completo estiver rodando.

## Política inicial

| Controle | Valor implantado |
|---|---|
| Entrada | Até 10 candidatos do Trending recente, com identidade confirmada no registro PostgreSQL |
| Ativos / espera | Até 10 ativos e 20 em espera |
| token_info | No máximo uma chamada por 60 s; slots de Trending e Trenches preservados |
| Frequência por token | Meta de 600 s; erros, cooldown e indisponibilidade podem atrasar |
| Prioridade | Top 3 do Trending recebem bônus limitado a 120 s entre consultas já vencidas |
| Justiça | Vencimento mais antigo supera o bônus; admissão da espera segue ordem de chegada |
| Vencimento | Concessão ativa de 30 min, sem extensão automática por continuar no ranking |
| Saída / reentrada | Sair do ranking não cancela o ativo; ao vencer, uma nova observação pode recolocá-lo atrás dos que já esperavam |
| Frescor | Recebido/aceito nos últimos 720 s; primeira consulta e atraso são explícitos |

A espera vence 30 min após a última descoberta observada. Tokens além das 30 vagas são adiados com contador de ocorrências; não são considerados acompanhados. Linhas expiradas da agenda podem ser reutilizadas. Identidades e capturas originais continuam no registro/histórico permanente da base; a agenda não é um histórico de decisões de admissão.

O padrão comporta dez tokens a aproximadamente uma consulta individual por dez minutos. Um único token também conserva a meta de dez minutos; slots ociosos não aumentam sua frequência. Vinte ativos exigiriam pelo menos vinte minutos por giro no mesmo orçamento e **não estão habilitados**. Milhares de identidades cadastradas não recebem acompanhamento automático.

O orçamento nominal dos três endpoints permanece 5 unidades de peso/minuto, com limitador local de 1 unidade/s e sem burst no restart. Não se presume quota exclusiva da conta nem se aumenta a taxa de outros consumidores. O perfil financeiro/Telegram não pertence a este recorte.

## Implementação e recuperação

- `src/midas_gmgn/token_schedule.py`: tabela/index no SQLite já existente; nenhum serviço ou dependência nova. Chave por fonte, chain e endereço; referência imutável ao registro e referência à descoberta.
- `data_collector.py`: sincroniza uma identidade local por tick de 5 s, com corte obtido de `/v1/clock`; não varre o registro completo. Falha na consulta local adia novas admissões e mantém coleta dos ativos e entrega.
- A reserva da tentativa, próxima janela global e trabalho do token compartilham transação. O sucesso da agenda e o bruto pendente também são atômicos. ACK perdido reutiliza UUID/hash; recebimento e aceitação são relógios separados.
- Erro de um token tem backoff próprio até 900 s. Os demais conservam slots. HTTP 429/401/403 mantém o cooldown global persistente existente. Tentativa interrompida vira retry visível sem zerar contadores ou liberar burst.
- SQLite continua em WAL/FULL e com proprietário único. Contratos conferidos nas documentações de [transações](https://www.sqlite.org/lang_transaction.html) e [WAL](https://www.sqlite.org/wal.html).

`GET /v1/status` → `collector.tracking` mostra ativos/espera, fila antes da primeira consulta, recebidos/aceitos frescos, atrasos, próxima consulta, expiração, tentativas, erros e referências das capturas. `current` exige todos os ativos frescos e aceitos. Isso não certifica campos econômicos ou conclusão dos destinos; estes conservam seus próprios recibos/status. Processo parado invalida `current`. `collector.source_current` usa a frequência concedida por token, para não declarar atraso só porque um slot estava ocioso.

`/v1/tokens` continua sendo o registro: `tracking_enabled=false` descreve que o registro não agenda automaticamente todos os tokens. O novo `tracking_scope` direciona ao status do coletor.

Configuração em `compose.data.yaml`: `DATA_TRACKING_MAX_ACTIVE=10`, `DATA_TRACKING_MAX_WAITING=20`, `DATA_TRACKING_REFRESH_SECONDS=600`, `DATA_TRACKING_LEASE_SECONDS=1800`. Configurações incompatíveis com o orçamento são recusadas no startup. Horários armazenados em epoch UTC, capturas em ISO UTC.

## Validação

**68 testes aprovados:** 54 do coletor/cliente em Docker Python 3.13, rede desligada; 14 de status/registro, incluindo três integrações PostgreSQL com schemas exclusivos. Os casos cobrem duas voltas da agenda em relógio simulado, justiça entre os dez ativos, quota de descoberta, retry, fila cheia, vencimento/reentrada, erro de disco com rollback, ACK fora de ordem, indisponibilidade e retomada. A primeira execução local esbarrou na pasta pytest compartilhada sem permissão; pasta exclusiva passou.

**Restart real:** 12 registros preservaram todos os campos de agenda comparados e SHA256 `a38307a8b479bfc35d2368061bee5a1d6753eb5efd16a3272c99a0266238a7cb`; pendências e próxima janela token_info iguais. O primeiro probe de volume parado falhou ao abrir WAL somente leitura; corrigido para leitura immutable apenas após confirmar processo parado e WAL ausente/vazio. Serviço reiniciado nos dois casos; nenhuma alteração dos dados pelo probe.

**Validação real concluída às 18:37:06 UTC:** 10/10 ativos recebidos e aceitos frescos, nove tokens em espera, zero primeiras consultas pendentes e `tracking.current=true`. Dez endereços conferidos diretamente no bruto, SHA256 correto e recibos aprovados em Lake/ClickHouse. Registro e pipeline atuais, filas de coletor, normalização, registro, outbox e destinos zeradas na amostra. Os contadores globais de falha do coletor permaneceram zero; o contador de dois slots perdidos por endpoint já existia no primeiro snapshot desta prova e não aumentou. Não equivale a cobertura contínua entre consultas.

Checagem pontual 3.1: antes do rollout, coletor 47,6/256 MiB, worker 1,33/1,465 GiB. Após restart e aquecimento curto, coletor 17,37/256 MiB, worker 329,9 MiB. O valor menor após restart não prova correção de vazamento nem capacidade sustentada. Não houve teste de carga, medição de crescimento diário ou ampliação de memória/quota.

## Operação e próximo recorte

API/worker: `midas-data-app:tracking-v1-20260910`. Coletor: `midas-data-collector:tracking-v2-20260910`. Tags persistidas no env privado da base, sem alterar credenciais. PostgreSQL, NATS, ClickHouse e serviços fora da base preservados.

Evidências em `.scratch/checkpoint-token-tracking-20260910/`: XMLs, builds, deploys, probes, snapshots e comparação do restart. Imagens anteriores preservadas; a tabela SQLite é aditiva. Retroceder para a imagem antiga do coletor desativa a agenda e volta à rotação antiga, portanto não é retomada equivalente.

Próximo recorte: **4.1**, homologar os campos econômicos usados na trajetória. Curvas, marcos, estratégias, UI e medições de escala continuam posteriores.
