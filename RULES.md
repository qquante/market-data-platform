# 5.2.1 — comparações versionadas sobre janelas compartilhadas

**Concluída e implantada em 10/09/2026**, CP-RULES-01, A0, sem subagentes. A **5.2 permanece em andamento**: este recorte entrega a condição e sua explicação. Próximo: **5.2.2**, registro de regras ativas, estado persistente, debounce/cooldown e disposição de emissão ou abstenção.

## Entrega mínima

[rules.py](midas_data/rules.py) define uma comparação entre uma das quatro features da 5.1 e um limite decimal. A [API](midas_data/api.py) expõe `POST /v1/rules/evaluate`, autenticado, com corpo limitado a 8 KiB e resposta `Cache-Control: no-store`.

A avaliação lê a janela pronta em memória pelo mesmo contrato de disponibilidade dos outros consumidores. Não executa SQL, registra adesões, solicita coleta, escreve histórico ou emite ações. Não há nova tabela, serviço, biblioteca, fila ou cache de regras. O parse do frame e a comparação ocorrem a cada chamada; compartilhamos o cálculo das features e a consulta de trajetória. Escala de centenas de regras continua sem benchmark.

Exemplo de corpo (o token e o limite são parâmetros do consumidor):

```json
{
  "version": "gmgn-feature-comparison-rule-v1",
  "window": {
    "source_kind": "gmgn",
    "chain": "sol",
    "token": "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
    "method": "gmgn.market_rank.v4.market_cap",
    "window_seconds": 900,
    "gap_after_seconds": 120
  },
  "feature": "market_cap_latest",
  "operator": "gte",
  "threshold": "100000",
  "unit": "usd",
  "quality_policy": "strict_feature_coverage_v1"
}
```

A janela precisa ter sido registrada pela rota da 5.1.2. Avaliar uma dependência não registrada responde `unknown` e não cria a adesão. O exemplo não ativa uma estratégia de negociação.

| Contrato | Comportamento |
|---|---|
| Features | `market_cap_latest`, `market_cap_min_observed`, `market_cap_max_observed`, `market_cap_sample_change_pct` |
| Operadores | `gt`, `gte`, `lt`, `lte`, `eq`, `ne`; nenhuma expressão executável |
| Limite | String decimal comum, até 160 caracteres; sem float, expoente, NaN ou infinito |
| Unidades | `usd` para market cap; `percent` para variação; incompatibilidade retorna 422 |
| Resultado | `state: "true" / "false" / "unknown"`, motivo e evidência dos dados/gates |
| Identidade | SHA-256 da definição canônica: versões de regra, janela, features e trajetória; fonte, chain, token, método, janela/gap, feature, operador, limite, unidade, política e gates |
| Proveniência | ID da avaliação, regra, janela, frame, geração, corte, próxima atualização, dependências, pontos de entrada, gate da última observação e contadores de qualidade |
| Repetição | Mesma regra e mesmo frame pronto produzem a mesma resposta; uma nova geração tem identidade de avaliação própria |

Zeros e casas decimais redundantes no limite convergem para a mesma identidade, sem arredondamento pelo contexto Decimal ambiente. A normalização de chain/endereço e gap padrão reutiliza a configuração da janela. Guarde o JSON de entrada para repetir a consulta: o campo de saída `definition` acrescenta dependências e gates calculados pelo servidor, e não é o corpo de entrada.

## Política de qualidade

Toda regra exige janela utilizável e última observação disponível/fresca. Pendência, lease expirado, perda do dono, polling vencido ou prazo do frame atingido respondem `unknown`; valores antigos não são usados como fallback.

A comparação de `market_cap_latest` depende da última observação: uma lacuna anterior não invalida automaticamente uma cauda válida e fresca. Para mínimo, máximo ou variação, a janela também precisa estar sem pontos indisponíveis, empates ambíguos ou gaps sinalizados. Mínimos e máximos continuam sendo extremos **observados**; ausência de gap sinalizado não prova continuidade entre amostras. A variação mantém as restrições da 5.1, incluindo base diferente de zero e duas observações elegíveis.

Dados faltantes, revisão invalidada, unidade de feature inesperada ou versão de frame incompatível causam abstenção com motivo. `false` significa comparação feita e não satisfeita. Não significa dado ausente nem autorização/supressão de emissão.

## Validação

**166 testes aprovados**, zero falhas, erros ou skips, na imagem candidata Python 3.13; duração de 11,532 s. São 41 novos casos de regras e 125 regressões, com novas asserções no teste integrado de janelas.

- [test_rules.py](tests/test_rules.py): seis operadores, precisão decimal, identidade canônica, quatro features/unidades, gaps, revisão inválida, ambiguidade, base zero, frescor, limites de entrada, autenticação e 20 consumidores HTTP com SQL proibido na fixture.
- [test_feature_windows_integration.py](tests/test_feature_windows_integration.py): PostgreSQL real em schema privado, dados sintéticos; publicação muda a referência da avaliação, resultado muda de false para true quando o dado cruza o limite, falha de checkpoint dá unknown, reconexão retoma a condição e vencimento abstém.
- Regressões de trajetória, features, janelas, resumos UTC, cruzamentos, contexto econômico e corte histórico passaram na própria imagem que foi implantada.

A prova real usou a janela Trending de 900 s já existente, com gap de 120 s, sobre o token do exemplo. Na geração **53**, **20 avaliações concorrentes** retornaram a mesma decisão e causaram **zero consultas adicionais de trajetória**. O teste usou limites técnicos (`>= 0` / `< 0`) sobre market cap positivo para verificar true/false; não é uma estratégia de entrada. Unknown foi conferido com uma janela de 901 s não registrada, que permaneceu não registrada.

O frame real correspondeu integralmente à consulta histórica no mesmo corte. Três respostas históricas anteriores permaneceram idênticas. Aceite terminado em **2026-09-10T21:40:46.742446Z**.

A mesma adesão foi renovada até **2026-09-10T22:10:46.527907Z** (19:10:46 de Brasília), sem renovação automática:
`29eab9f4e4952fbd479f80ebf3fe4ec2041a39485dc60d69ff1e4f15570a9d55`.

API implantada: `midas-data-app:rules-v1-20260910`, SHA `50c1965d00744a09f83352dd50312d408495f0e544ab7676a2b781caee1b779f`. Somente seu container mudou; os cinco demais mantiveram ID e imagem. Os seis ficaram saudáveis. Nenhuma migração foi adicionada.

## Ainda falta na 5.2

O chamador envia a definição a cada consulta. Não há cadastro persistente de regras, avaliação automática por atualização, debounce com cobertura, cooldown, registro durável de consumo/avaliação ou emissão de sinal. A resposta é determinística e traz referências, mas o checkpoint de janela conserva apenas a última projeção; isso não constitui histórico auditável de todas as avaliações.

A **5.2.2** deve conectar um conjunto limitado de regras ativas às publicações e vencimentos, persistir seu estado, aplicar debounce/cooldown e registrar atomicamente a disposição e o motivo. A atualização de dados deve continuar durante cooldown; desconhecido ou gap não pode contar como duração de condição verdadeira. Paper trading continua na **5.3**.

Evidências: [checkpoint-rules-20260910](../.scratch/checkpoint-rules-20260910/), [testes finais](../.scratch/checkpoint-rules-20260910/contract-tests.xml), [aceite real](../.scratch/checkpoint-rules-20260910/live-acceptance.json), [decisões reais](../.scratch/checkpoint-rules-20260910/real-decisions.json), [frame consumido](../.scratch/checkpoint-rules-20260910/real-frame.json), [runtime](../.scratch/checkpoint-rules-20260910/runtime-acceptance.json). Backups dos arquivos editados, script de prova e baseline também estão nesse diretório.
