# Aceite operacional GMGN 6.1–7.4 — v1 implantada

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

CP-RESEARCH-UI-LIVE-02. Aceite consolidado em 2026-09-11T10:42:36.050612+00:00 (UTC). A0, execução sem subagentes.

**7.4 concluída no escopo operacional v1.** O usuário respondeu “finalzie entao” após o relatório explicar exatamente a implantação e a coleta contínua pendentes. Essa autorização foi aplicada a API/worker/coletor de midas-data-base; a revisão automática permitiu a operação. A continuação horária permanece removida.

## Implantação

- API e worker: `midas-data-app:research-ui-v1-20260911`, imagem `sha256:72082e1c41acbd6e1008bfda4c45169dbac6f88b6a223b073acbcf3e5114a05f`.
- Coletor: `midas-data-collector:research-schedule-v1-20260911`, imagem `sha256:ebdb97f948d32f6388899245ea9c42930961c8ad3f2c3bbc68f83a115938d69c`.
- `DATA_RESEARCH_ENDPOINTS=token_top_holders`; intervalo global existente de 60 s, orçamento compartilhado, um slot de holders por minuto e alvo de revisita de 600 s. Dez tokens ativos; perfil Solana/read-only.
- Migrations aditivas 016–018 aplicadas. Seis serviços saudáveis, sem OOM. Hashes de todos os módulos/recursos do runtime conferidos dentro de API, worker e coletor contra os manifestos testados.
- Nenhuma alteração de código de runtime nesta rodada. Configuração anterior salva em `C:\Users\PC\Desktop\Midas\.data\gmgn-data\pre-research-20260911T103031Z`. Bancos/volumes preservados; nenhum outro projeto Compose foi alvo de comandos de implantação.

## Provas na base real

| Verificação | Resultado |
|---|---|
| Captura contínua de holders | 11 capturas / 10 tokens / 1.100 linhas; todas ready, zero issues e zero falhas do endpoint |
| Quota observada | Menor intervalo entre requisições: 60,008051 s; não se acelerou a agenda para o teste |
| Frescor e publicação | Os 10 tokens com holders dentro de 720 s; maior received_at → available_at: 2,867717 s; uma revisita já observada |
| Proveniência | Bytes originais recuperados pela API e SHA-256 conferidos para as 11 capturas |
| Canário salvo | Captura 5a0ffc4d-c431-4cf1-b4f8-ac338a50d4e1, obtida às 03:01:25Z, ingerida com relógios originais; duas submissões produziram 1 captura, 1 interpretação e 10 holders |
| Consulta temporal | Corte no recebimento antigo retorna 404 porque o dado ainda não tinha sido publicado; corte atual retorna as 10 linhas |
| Destinos | Recibos de lake/ClickHouse aprovados para os lotes de zero fatos; holders são projetados no PostgreSQL, sem alegar projeção analítica nesses destinos |
| Retomada | Worker parado, job de 17 membros enfileirado e submissão duplicada reconhecida; após subida concluiu ready. Coletor reiniciado, capturas/agenda anteriores preservadas e novas capturas confirmadas |
| Consumo anterior | Oito rotas de leitura de conta/posições/vínculos/demandas paper, cadastro, ingestão e pesquisa responderam 200 |
| Preservação | Seis hashes de ledgers paper idênticos ao baseline; zero regras/vínculos habilitados e zero posições abertas |
| Filas | Entrega, normalização, lake e ClickHouse zeradas; nenhum erro pendente no aceite |

A primeira consulta do harnês de aceite a `/v1/tokens` omitiu `knowledge_cutoff` e recebeu 422; uma montagem posterior sobrescreveu os parâmetros da URL. Corrigido apenas o harnês, mantendo fonte/cadeia/limite/corte; os oito consumidores passaram. Não houve patch da API para relaxar o contrato.

## Pesquisa e reprodução

Dois jobs reais concluídos: 17 membros para a prova de fila/retomada e 98 membros para consumo/exportação/comparação. A segunda população contém todos os primeiros registros GMGN/Solana de uma janela de seis minutos que coube no limite de 100. A janela foi escolhida por tamanho e antiguidade, sem filtro de resultado; é amostra de conveniência para homologação, não seleção representativa do mercado.

Foram executadas duas estratégias e três estudos descritivos (transições, evento e vizinhos de prefixo), com custos explícitos de cenário e splits cronológicos. **98 entradas e 98 labels por nível ficaram unknown**, pois o método `gmgn.trenches.v1.usd_market_cap` exige um campo ausente nessa evidência. Os aliases `market_cap` e `price` não foram promovidos sem homologação. O split conservador purgou 97 membros e conservou 1 no test, também sem cotação elegível. Não existem trades simulados informativos ou rentabilidade demonstrada nessa amostra. Essa limitação é visível no manifesto; é o comportamento correto dos contratos existentes, descritos em [ECONOMIC_INPUTS](ECONOMIC_INPUTS.md).

Submissão repetida reconheceu a mesma tentativa. Bundle e resultado baixados pela API/navegador têm hashes iguais; reprodução em container novo **sem rede** foi byte a byte idêntica. SHA-256 do arquivo de resultado: `3cd5463d717608ad5dd14500cb3f87435f31876ac93dd9667cb159326c41a45d`. Não confundir esse hash do arquivo com o campo interno result_sha256, que exclui o próprio campo do cálculo.

Reprodução do artefato congelado, sem provider ou credenciais:

```powershell
docker run --rm --pull never --network none --read-only --tmpfs /tmp --entrypoint python -v 'C:\Users\PC\Desktop\Midas\.scratch\live-acceptance-20260911:/evidence' midas-data-app:research-ui-v1-20260911 -m midas_data.experiments --bundle /evidence/live-bundle.json --spec /evidence/live-spec.json --output /evidence/live-replay.json --verify /evidence/live-result.json
```

## Painel publicado e acesso

[http://127.0.0.1:8791](http://127.0.0.1:8791). A chave existente está no campo DATA_API_KEY de `.data/gmgn-data/base-runtime.env`; não a colocar em URL, documentação ou screenshots. O formulário mantém a chave apenas em memória e Sair encerra a sessão local.

Chromium real em 1440×1050 e 390×844: cartões operacionais, trajetória com três pontos elegíveis do token acompanhado, detalhe da captura, holders com 100 linhas, lista de posições paper e artefatos de pesquisa aprovados. Zero erros de página, sem overflow horizontal mobile, armazenamento local/session vazio; `/v1/status`, `/docs` e `/openapi.json` retornam 401 sem credencial. Screenshots desktop/mobile inspecionados. Não se usou o preview fixture da porta 8794.

## Operação e reversão

O coletor permanece ativo. A agenda Codex de hora em hora foi removida; são mecanismos distintos. Reabrir a task não é necessário para o coletor continuar no Docker.

Para conferir o estado sem imprimir credenciais:

```powershell
docker compose -p midas-data-base --env-file .data/gmgn-data/base-runtime.env -f compose.data.yaml --profile collection ps
```

Para pausar todas as consultas externas deste coletor, preservando dados e agenda:

```powershell
docker compose -p midas-data-base --env-file .data/gmgn-data/base-runtime.env -f compose.data.yaml --profile collection stop collector
```

Para desligar somente holders, deixe DATA_RESEARCH_ENDPOINTS vazio no env privado e recrie apenas collector com `up -d --no-deps --no-build --pull never collector`. Não apague spool/cooldown ou volumes.

Rollback operacional: restaurar o env privado do diretório de backup acima e usar o arquivo `rollback.compose.json` desse mesmo diretório como segundo `-f`, depois de `compose.data.yaml`, para aplicar as imagens anteriores de cada serviço com `up -d --no-deps --no-build --pull never api worker collector`. O override é necessário porque API e worker tinham imagens anteriores diferentes. Preserve migrations 016–018 e os dados novos; não faça downgrade destrutivo de schema. Esses comandos de reversão são instruções, não foram executados neste aceite.

## Limites mantidos

- 987 testes únicos do checkpoint local aprovado: 905 plataforma, 23 SQL e 59 coletor. Runtime idêntico foi conferido; esta rodada executou implantação e aceites reais, sem repetir a suíte inteira.
- 23 gates de backup PowerShell host continuam fora do recorte dispensado anteriormente; não há alegação de novo backup/restore integral.
- Carga 1/20/100 e retenção/cancelamento foram provadas no checkpoint isolado. A observação real atual é curta; não certifica SLO sustentado ou capacidade global. Consultas pesadas continuam limitadas a duas simultâneas, excedente 429.
- Universo depende das descobertas/coleta admitidas. Intervalos sem observação e campos sem contrato continuam desconhecidos. Qualificar cobertura econômica e coletar amostra madura é próximo trabalho de pesquisa.
- Traders/OHLCV/cadeias adicionais, corpus social Q14 e inferência avançada exigem escopos/contratos próprios. Não fazem parte do encerramento operacional v1.
- Nenhuma ordem real, assinatura, broadcast, nova regra financeira ou mensagem a terceiros.

## Artefatos

Pasta `.scratch/live-acceptance-20260911`: `acceptance-summary.json`, `containers.json`, `before.json`, `after-deploy.json`, `after-restart.json`, `canary.json`, `final-live.json`, `research-created.json`, `research-acceptance.json`, bundle/spec/result/replay, `browser-acceptance.json`, PNGs e harneses/comandos. Backups das docs em `before-docs/`. Não alterar o arquivo selado `checkpoint-final-20260911/release-source.zip`; ele documenta o checkpoint local anterior.

A rota/cliente permanecem ancorados na [fonte oficial GMGN do commit já homologado](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/src/client/OpenApiClient.ts), reconferida nesta rodada. Não houve mudança no protocolo do adapter.
