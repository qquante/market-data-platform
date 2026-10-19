# 5.3.4.2 — preço por token consumido pelo paper

> Continuação concluída: [5.4.1 — replay mínimo do ciclo paper](PAPER_REPLAY.md), 330 testes e comparação online/offline sem divergência. Próximo 5.4.2. O registro abaixo descreve o checkpoint anterior.

**Concluída e implantada — CP-PAPER-PRICES-01, 10/09/2026.** A etapa **5.3 está concluída no recorte de lotes paper com saída por tempo**, conta compartilhada e admissão por regras. A posição que havia ficado aberta fora do ranking foi fechada na simulação com preço real de token_info.

## Comportamento

Novos lotes e vínculos podem declarar `policy.version = "gmgn-paper-timed-lot-v2"`. Essa política usa exclusivamente o campo homologado `token_info.price.price` para entrada/saída. Supply e market cap não são necessários para validar esse preço. A janela da regra continua sendo a origem do sinal; o consumo de preço acompanha a posição e sobrevive à pausa da regra e à remoção/expiração dessa janela.

A política v1 e seu formato permanecem compatíveis, inclusive como padrão quando a versão não é enviada. Para usar o acompanhamento independente em novos lotes, o consumidor deve enviar v2 explicitamente. Vínculos antigos pausados mantêm suas definições.

O preço deve pertencer à mesma origem, rede, token e interpretação homologada; ser positivo, finito e expresso em USD/token; estar publicado no corte conhecido; e ter sido recebido após o prazo da ação. O limite de idade é o menor entre o configurado no lote e 720 segundos. Uma observação mais recente inválida ou empatada não permite recorrer a um preço anterior. Não existe interpolação nem fallback para ranking ou market cap.

Na v2, `entry_timeout_seconds - entry_latency_seconds` deve ser pelo menos 720 segundos. Isso compatibiliza o prazo de espera com a cadência atual, sem garantir preenchimento. O coletor continua limitado a uma chamada token_info por 60 segundos e frequência de 600 segundos por token. O consumo local verifica fatos publicados a cada 30 segundos; a operação continua condicionada à chegada de amostra elegível.

## Implementação pequena

[paper_prices.py](midas_data/paper_prices.py) mantém até quatro frames em memória, compartilhados por origem/rede/token, no dono e loop paper existentes. Consulta no máximo 50 pontos dos últimos 720 segundos, com payload limitado a 1 MiB por frame. Utiliza o leitor temporal e os contratos de valuation existentes, com corte protegido pela barreira de publicação. Falha ou frame vencido impede execução; os leitores de posições não disparam consultas de preço.

As posições sem saída devida ainda não precisam consultar esse preço. Ao terminar o lote, seu cache deixa de ser necessário e a rota de demanda já existente o retira do coletor. Nenhum serviço, biblioteca, conexão permanente ou chamada extra à GMGN foi introduzido nesta parte.

Frames consumidos continuam arquivados por SHA256 em `rule_frames`, vinculados ao evento paper. O crédito de saída usa a transação existente de posição, evento e conta.

## Alteração explícita da posição antiga

`POST /v1/paper/positions/{position_id}/price-source` aceita:

```json
{
  "version": "gmgn-paper-token-info-price-v1",
  "expected_policy_id": "<hash da politica original>",
  "reason": "Motivo explicito da troca de fonte para a saida futura"
}
```

Apenas uma posição v1 aberta pode receber essa alteração. A [migration 015](midas_data/migrations/015_paper_price_source.sql) adiciona o tipo de evento `price_source_changed` à restrição existente; não cria tabela nem reescreve eventos.

O registro inclui definição/hash da nova fonte, identidade, instante UTC de vigência, revisão, motivo e referências à política/janela originais. Ele é acrescentado ao estado e ao ledger, sem mudar definição original, entrada, quantidade, custos ou saldo. Só uma cotação recebida a partir da vigência pode alimentar a saída. Repetir a mesma solicitação devolve o mesmo registro, inclusive após fechar o lote; uma alteração diferente é recusada.

Na retomada, identidade, política e registro do evento são conferidos antes de usar a nova fonte. A versão anterior da API não implementa essa semântica; depois de persistir alterações, uma reversão operacional precisa conservar seu leitor compatível, sem apagar os registros.

## Testes

**270 testes passaram, sem falhas ou skips, em 42,581 segundos na imagem final:** 256 regressões e 14 casos novos. Os testes de PostgreSQL usam schemas privados e preços sintéticos. O coletor não mudou nesta entrega.

Os novos casos cobrem política/identidade de fonte, prazo mínimo, preço sem supply, dado antigo/futuro/stale, empate, observação inválida, falha do leitor e descarte do cache, compartilhamento entre posições, 20 leitores sem novas consultas, admissão automática v2, independência da janela, rollback da alteração e perda de confirmação após commit. Também conferem saída, crédito único e restauração do estado terminal.

A revisão final corrigiu um diagnóstico que herdava a ausência de market cap mesmo quando o preço era válido. A imagem final reproduziu integralmente o resultado do frame real arquivado de saída no mesmo corte histórico; essa conferência de um frame não encerra o replay completo previsto na 5.4.

## Prova real da posição pendente

Execução exclusivamente simulada; cotações reais coletadas em modo read-only.

| Evidência | Resultado UTC |
|---|---|
| Posição | `f070bf0f79d5f709d9ded6fdc0828633dae24377db3465a1631350432a22a3e0` |
| Token | `42H7fBC2eNvjcRftqmVraxbPmiLzu3KnTpd5eRnspump` |
| Alteração persistida | 11/09/2026 00:46:03.281144Z, revisão 37 |
| API reiniciada com lote aberto | Recuperado às 00:46:34Z, aguardando cotação futura; conta ainda na revisão 6 |
| Cotação recebida | 00:47:06.836196Z, `0.0000026577919` USD/token |
| Saída simulada | 00:47:36.792291Z, revisão 42 da posição |
| Conta após saída | Revisão 7, um crédito de saída |
| Aceite após nova subida da API | 00:51:34Z, posição/eventos/conta iguais ao estado fechado |

Captura de saída: `93608e2e-34eb-4566-b99a-e99d980b2928`. SHA256 bruto: `49a9ffcbcfcda4e5e831aa258949bb223e0f87eb2562dee081dabeebb133ed06`. Token ausente nas três capturas de ranking mais recentes, com a coleta atualizada. Preço bruto, valuation, instante posterior à alteração, frame e aritmética decimal de saída foram conferidos.

A simulação retornou `11.48639523559742990366339477369862363522789816` USD ao caixa. O P&L deste lote foi `-8.51360476440257009633660522630137547993576584` USD. Saldo final da conta: `84.43999830569181152166612996038074110159817416` USD. Reserva e custo investido ficaram zerados; P&L acumulado `-15.56000169430818847833387003961925889840182584` USD. Esses valores conservam os custos/slippage da política original e não representam execução em mercado.

A entrada, sua cotação, política, janela original e eventos antigos foram preservados, assim como as outras três posições e os movimentos anteriores da conta. Terminaram **zero posições abertas e zero demandas paper**, com todas as regras e vínculos pausados.

## Runtime e próximo passo

API final `midas-data-app:paper-prices-v2-20260910`, SHA `6d36af349c641b76c380eed308d0aa27df3f55dbaafa3931b6be3cdb9608d184`. Cinco módulos e migration implantados conferidos contra o workspace; checksum 015 verificado. A tag da API foi atualizada no env local. Coletor, worker, PostgreSQL, NATS e ClickHouse mantiveram IDs/imagens; seis serviços saudáveis.

Evidências em `.scratch/checkpoint-paper-prices-20260910/`: `acceptance.json`, XML de testes, `before.json`, `amendment.json`, `resumed.json`, `closed.json`, `after-restart.json`, frame/bruto/valuation de saída, reprodução do frame pela imagem final, hashes implantados e estado dos serviços.

Próximo **5.4.1**: replay mínimo de um sinal e seu ciclo paper, comparando decisões, cotações, mudanças de fonte e movimentos do ledger com a execução online. Validação de 20/100 estratégias e medições sustentadas ficam nos recortes posteriores. Sem ordem real, assinatura, broadcast ou mensagem externa.
