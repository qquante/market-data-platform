# Carga, crescimento e retenção — 7.3

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

Checkpoint CP-RESEARCH-UI-01, 11/09/2026. Testes limitados executados; não é certificação de capacidade em produção ou de mercado instantâneo. Escopo de implantação em [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).

## HTTP real local / PostgreSQL fixture

Imagem final midas-data-app:research-ui-v1-20260911, prévia isolada, limite 0,5 CPU/512 MiB. Corpus de 30 capturas e um snapshot com dez holders. Corte fixo 2026-09-11T04:28:22.271334Z. Ferramenta: Node fetch concorrente contra 127.0.0.1:8794; sem chamadas GMGN.

| Leitura | Concorrência | Requisições | HTTP 200 | HTTP 429 | p95 das aceitas |
|---|---:|---:|---:|---:|---:|
| Snapshot holders | 1 | 10 | 10 | 0 | 50,47 ms |
| Snapshot holders | 20 | 40 | 40 | 0 | 132,82 ms |
| Snapshot holders | 100 | 200 | 200 | 0 | 604,54 ms |
| Trajetória/marcos | 1 | 10 | 10 | 0 | 135,56 ms |
| Trajetória/marcos | 20 | 40 | 2 | 38 | 138,98 ms |
| Trajetória/marcos | 100 | 200 | 2 | 198 | 255,32 ms |

Todas as respostas 200 tiveram bytes idênticos ao baseline do mesmo corte. Consulta serial posterior voltou a 200 e permaneceu idêntica. RAM cgroup: 47.894.528 → 55.279.616 bytes. Sem 500/timeouts no ensaio. Respostas: cerca de 17 KiB holders e 132 KiB trajetória.

**Trajetórias pesadas têm dois slots.** O resultado 2/200 aceitas não comprova suporte simultâneo a cem leituras pesadas. p95 com apenas duas amostras aceitas tampouco é SLO estatístico. 429 é o backpressure previsto; consumidor deve limitar concorrência e reagendar, conservando o mesmo corte quando deseja o mesmo resultado. A interface evita solicitações sobrepostas.

## Crescimento de pesquisa sintética

Harnês tests/research_capacity.py, sem rede, 0,5 CPU/512 MiB, três repetições por cenário. Cada token possui cinco registros de pontos entre séries de entrada/saída/outcome (com duplicações de referência); fixture inclui altas e perdas simuladas, quatro estudos, posições sobrepostas e casos sem holders. Não representa todo o histórico de um dia.

| Tokens | Estratégias | Mediana | Maior amostra | Dataset | Resultado |
|---|---:|---:|---:|---:|---:|
| 1 | 1 | 0,99 ms | 1,12 ms | 5.387 B | 16.321 B |
| 1 | 20 | 2,46 ms | 2,51 ms | 5.387 B | 80.297 B |
| 20 | 1 | 12,47 ms | 58,85 ms | 77.410 B | 106.315 B |
| 20 | 20 | 17,94 ms | 75,37 ms | 77.410 B | 296.432 B |
| 100 | 1 | 220,24 ms | 263,90 ms | 381.232 B | 1.036.843 B |
| 100 | 20 | 277,41 ms | 281,48 ms | 381.232 B | 1.295.493 B |

Pico RSS do processo: 54.411.264 bytes. Mesmos hashes nas três repetições e em reprodução independente da imagem instalada. Não se extrapola linearmente esse resultado para 20 mil pontos, população global ou meses de histórico. Jobs maiores esbarram nos limites explícitos antes de consumir recursos sem limite.

## Retenção e recuperação

- 64 coortes/128 experimentos retidos, incluindo falhas. Corrida de oito admissões com só uma vaga livre admitiu exatamente uma e preservou as demais tentativas existentes; excedentes retornam conflito.
- Membership congelado, hashes por token e por artefato. Interrupção após primeiro membro conservou checkpoint; retomada calculou apenas o segundo. Um owner expirado, inclusive falhando tardiamente, não sobrescreveu o novo resultado.
- Fila de famílias: teto de cem jobs, apenas tokens ativos elegíveis; raw pendente impede repoll/descarte. Expiração, 429, falha de persistência, ACK atrasado e restart testados com provedor fixture.
- Heartbeat limita linhas de família a vinte e publica total/has_more. Retenção de raw do coletor permanece limitada por backlog em bytes (padrão 512 MiB); a base usa reserva de espaço livre (padrão 4 GiB).
- Não existe purge automático do histórico bruto nem TTL de pesquisa. Atingir orçamento exige exportar, revisar retenção e aprovar uma ação explícita. JSON dos bundles e checkpoints é duplicado intencionalmente para retomada/exportação; limites lógicos não são medida exata do custo físico de JSONB, índices ou WAL.
- Nenhum volume/tabela real foi excluído. Schemas de teste são isolados e removidos no encerramento. Backup/migração histórica continuam fora deste recorte por decisão anterior do usuário.

Próxima medição útil depois da implantação: taxa real de captura, frescor por família, ocupação de disco por dia, idade de filas e consultas dos consumidores. A quota e a frequência do provedor continuam sendo limites reais; um painel rápido não torna a coleta instantânea.

Evidência: .scratch/checkpoint-dashboard-20260911/http-load.json; .scratch/checkpoint-final-20260911/research-growth.json, packaged-platform.xml, packaged-sql.xml, collector.xml, packaged-replay.json.
