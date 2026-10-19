# 5.2.2 — regras ativas, timers e histórico de decisões

**Concluída e implantada em 10/09/2026**, CP-ACTIVE-RULES-01, A0, sem subagentes. Fecha a **5.2 no recorte mínimo de regras sobre market cap mais recente**. Próximo: **5.3.1**, primeira posição paper com entrada, saída e ledger simulados.

## O que está funcionando

Uma definição pode ser registrada e permanece no PostgreSQL. O mesmo dono das janelas compartilhadas avalia a regra quando sua janela muda, fica indisponível ou seu timer vence. Consumidores consultam o último resultado em memória; não comandam a avaliação.

O cadastro ativo v1 aceita `market_cap_latest`, os seis comparadores da 5.2.1 e debounce/cooldown explícitos. A restrição a esse campo permite verificar todos os valores observados durante o debounce, inclusive um valor falso que chegou no mesmo lote que a cauda verdadeira. Comparações pontuais das quatro features continuam na rota anterior. Não foi criado motor de expressões genéricas.

- [rule_timers.py](midas_data/rule_timers.py): definição versionada e política temporal determinística.
- [active_rules.py](midas_data/active_rules.py): cadastro, atualização afetada por janela/timer, estado, pausa e persistência.
- [feature_windows.py](midas_data/feature_windows.py): integração no dono e no loop existentes.
- [011_active_rules.sql](midas_data/migrations/011_active_rules.sql): definições/estado, frames compartilhados imutáveis e avaliações compactas.
- [api.py](midas_data/api.py): registro, status, pausa, histórico paginado e recuperação do frame consumido.

| Limite do recorte | Contrato |
|---|---|
| Definições armazenadas | 16 no total, incluindo pausadas; mesmo conteúdo retorna o mesmo ID |
| Dependências | Até quatro janelas já registradas na 5.1.2 |
| Adesão de janela | Continua com lease de 30 min; a regra não renova nem cria a janela automaticamente |
| Configuração HTTP | Até 8 KiB; unidades, versões, política e campos validados |
| Debounce | 0 a 86.400 s, obrigatoriamente menor que a janela retida |
| Cooldown | 0 a 86.400 s |
| Timer | Verificado no loop existente, nominalmente a cada 2 s, sujeito ao tempo de processamento |
| Consulta de histórico | Até 100 avaliações por página, com cursor de sequência |
| Frame arquivado | Até 4 MiB; um payload por hash compartilhado entre regras |

Não há novo serviço, conexão permanente adicional, biblioteca ou chamada à GMGN. O loop compara no máximo 16 assinaturas em memória; regra inalterada e sem timer vencido não é reavaliada nem gravada. Registrar regras depende de uma janela existente. A quota de coleta permanece a mesma.

## Semântica temporal

`true_since` começa no instante UTC da avaliação, sem crédito retroativo por uma condição anterior à ativação. Com debounce positivo, atingir o prazo leva a uma revalidação. A emissão também exige observações elegíveis cobrindo até o fim desse período, dentro do gap tolerado pela janela, sem ponto inválido ou empate ambíguo. Um valor observado que não satisfaz a comparação interrompe a tentativa, mesmo se a última amostra do lote já satisfaz.

Silêncio não comprova duração: o timer pode resultar em `awaiting_observation_coverage`. Um gap, perda da borda necessária ou dado invalidado reinicia a contagem. Isso é cobertura das amostras recebidas localmente, não prova de que o mercado permaneceu continuamente acima/abaixo do limite entre amostras.

A política de emissão é `once_until_observed_false_v1`: depois de um sinal, somente uma condição **false válida** rearma outra emissão. Unknown, pausa, reativação e reinício conservam essa trava. Cooldown pode suprimir uma nova condição já confirmada, mas os dados e o estado continuam sendo atualizados; o prazo pode liberar um sinal sem novo preço se o dado ainda estiver utilizável.

Reinício/reconexão e interrupção de pelo menos 10 s no loop reiniciam debounce pendente. O registro informa a retomada e o último instante avaliado, sem contar o intervalo parado como sustentação. Estado de cooldown e trava de sinal sobrevivem.

| Disposição | Motivos principais |
|---|---|
| `pending` | Debounce em curso, espera de cobertura, cobertura reiniciada ou condição interrompida |
| `abstain` | Condição falsa ou dado desconhecido; motivo do predicado é preservado separadamente |
| `suppressed` | Cooldown ou sinal já produzido sem novo false válido |
| `signal` | Condição confirmada e emissão elegível; **registro interno**, sem ordem, fill ou mensagem externa |

A avaliação separa condição bruta, estabilização confirmada nesta avaliação, elegibilidade, disposição e motivo. No caminho `already_signaled`, não se calcula novamente uma confirmação de duração: a trava já impede outro sinal.

## Persistência e retomada

A identidade da regra inclui a comparação canônica e suas versões/dependências/gates, mais versão ativa, debounce, cooldown, política de continuidade e emissão.

Cada atualização grava, **na mesma transação**, o frame compartilhado quando necessário, a avaliação compacta e o estado/última decisão da regra. Sequência por regra e ID de evento determinístico impedem duplicar uma gravação. A atualização do estado exige a sequência anterior esperada. A memória só é atualizada depois do commit.

O ledger referencia um frame imutável completo, com SHA-256 conferível, em vez de copiar o payload por regra. O frame preserva corte, método, geração, pontos, revisões e referências aos fatos/raw. Isso registra o que o motor consumiu; o checkpoint de janela continua conservando apenas sua projeção mais recente.

Falha antes do commit não deixa sinal, estado ou frame órfão da transação. Se o commit aconteceu e a confirmação se perdeu, a recuperação lê o estado persistido e conserva a trava de emissão. Não há entrega para bus, Telegram ou executor; o evento interno ainda não é uma posição paper.

Pausar registra a decisão de pausa e desabilita a regra. A definição e o histórico são mantidos; reenviar a mesma definição a reativa com a trava/cooldown anteriores. Não há endpoint de exclusão de histórico neste recorte.

## API

Todas as rotas exigem o Bearer existente.

- `POST /v1/rules/active`: registra ou reativa; 202. Mesmo cadastro já ativo é idempotente.
- `GET /v1/rules/active`: status em memória, `last_recorded_decision` e `evaluation_pending`; não apresenta uma decisão antiga como recém-avaliada.
- `POST /v1/rules/active/{rule_id}/pause`: pausa idempotente; 204.
- `GET /v1/rules/active/{rule_id}/evaluations?limit=50&before_sequence=...`: histórico em ordem decrescente.
- `GET /v1/rule-frames/{frame_key}`: bytes originais do frame consumido.
- `POST /v1/rules/evaluate`: permanece a avaliação pontual da 5.2.1, sem cadastro.

Corpo de registro:

```json
{
  "version": "gmgn-active-latest-rule-v1",
  "comparison": {
    "window": {
      "source_kind": "gmgn",
      "chain": "sol",
      "token": "5M8cjsPtV6q5DeuShVBhGohTttY6Cbk597dRQptQpump",
      "method": "gmgn.market_rank.v4.market_cap",
      "window_seconds": 900,
      "gap_after_seconds": 120
    },
    "feature": "market_cap_latest",
    "operator": "gte",
    "threshold": "100000",
    "unit": "usd"
  },
  "debounce_seconds": 10,
  "cooldown_seconds": 60,
  "emission_policy": "once_until_observed_false_v1"
}
```

O limite do exemplo é ilustrativo. A prova real usou `>= 0`, debounce de 3 s e cooldown de 10 s como teste técnico, sem estratégia de negociação.

## Testes e prova real

**185 testes passaram**, zero falhas, erros ou skips, em **17,161 s**, na imagem Python 3.13 candidata. São 19 novos casos e 166 regressões.

[Testes temporais](tests/test_rule_timers.py) usam relógios UTC explícitos. A [integração](tests/test_active_rules_integration.py) usa PostgreSQL real em schema privado, com capturas sintéticas e o loop efetivo: timer sem preço, cobertura, false/rearme, cooldown, pausa/reativação, limites, publicação, gap de runtime, transação interrompida e confirmação perdida após commit. Também verifica 20 leitores com SQL proibido, compartilhamento e hash de frame, paginação e retomada sem duplicação.

O primeiro caso real, sobre `HzJQbqpQcgMGocE1K3yWQ4ZfN6eXruy4TqJg2tSoitt`, não recebeu uma observação nova nos 100 s do aceite. Passou para unknown por envelhecimento e foi pausado **sem sinal**. A falha de obter cobertura está preservada em [first-canary.json](../.scratch/checkpoint-active-rules-20260910/first-canary.json); nenhum teste unitário ou integrado falhou.

A seleção seguinte usou o token do exemplo, presente nas dez capturas mais recentes. O timer real registrou espera de cobertura sem novo preço. Depois, uma observação nova permitiu **um sinal interno**, às **2026-09-10T22:08:38.432721+00:00**. O frame arquivado correspondeu à consulta histórica no mesmo corte. **20 leitores de status causaram zero consultas adicionais de trajetória**. A API foi reiniciada; a regra foi recuperada sem recadastro e o sinal não foi duplicado. Três respostas históricas anteriores permaneceram iguais. Aceite encerrado às **2026-09-10T22:11:32.994438+00:00**. Ambas as regras técnicas ficaram pausadas. A primeira janela técnica foi removida; a segunda adesão expira em **2026-09-10T22:37:59.170891+00:00**, sem renovação automática.

Runtime: API `midas-data-app:active-rules-v1-20260910`, SHA `eb97df40690bb8ec460f957b950b44eff879d2a760cc462779ff51c68ae9fb56`. Somente a API mudou; os cinco outros containers mantiveram ID e imagem e os seis ficaram saudáveis. O código implantado foi comparado por SHA-256 com os arquivos do workspace.

Evidências: [checkpoint-active-rules-20260910](../.scratch/checkpoint-active-rules-20260910/), [testes finais](../.scratch/checkpoint-active-rules-20260910/contract-tests.xml), [aceite real](../.scratch/checkpoint-active-rules-20260910/live-acceptance.json), [antes do reinício](../.scratch/checkpoint-active-rules-20260910/before-restart.json), [após reinício](../.scratch/checkpoint-active-rules-20260910/after-restart.json) e [runtime](../.scratch/checkpoint-active-rules-20260910/runtime-acceptance.json).

## Próximo recorte

**5.3.1:** consumir o sinal interno uma única vez para abrir uma primeira posição simulada, fechar com uma política explícita e registrar entradas/saídas no ledger paper. Preços, cortes, custos e limitações de cobertura precisam estar visíveis; nenhuma operação real.

A 5.2 está concluída somente neste recorte de comparações sobre a última observação. Expressões compostas, debounce de estatísticas móveis, outras famílias de dados, equivalência online/replay, carga de 20/100 estratégias e retenção de longo prazo continuam fora deste aceite. Frames e decisões históricos não têm limpeza automática; o crescimento deve ser acompanhado ao ampliar o uso, conforme 7.3.
