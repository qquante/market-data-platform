# 5.1.2 — janelas compartilhadas por publicação e vencimento

**Concluída e implantada em 10/09/2026**, CP-FEATURE-WINDOWS-01, A0, sem subagentes. Fecha a **5.1 no recorte mínimo de até quatro janelas ativas**. Próximo: **5.2**, regras versionadas com true/false/unknown.

A nova rota de consumo entrega um resultado pronto em memória. Uma atualização compatível lê a trajetória uma vez, calcula as [features da 5.1.1](SHARED_FEATURES.md) e publica o mesmo payload para todos os consumidores. A consulta histórica `/v1/trajectory/features` continua disponível com o corte explícito e seu comportamento anterior.

## Estrutura mínima

- [feature_windows.py](midas_data/feature_windows.py): um atualizador no processo da API, com polling compartilhado e vencimentos.
- [010_feature_windows.sql](midas_data/migrations/010_feature_windows.sql): uma tabela de configuração/checkpoint/último payload e um índice na disponibilidade das normalizações.
- [api.py](midas_data/api.py): ciclo de vida, registro, status, leitura e remoção da janela.

Não foi alterado o caminho de gravação do normalizador, o coletor, o worker, a quota ou o bus NATS. A fonte dos eventos é o registro durável de publicações já existente no PostgreSQL; não há novo serviço ou biblioteca.

| Limite inicial | Valor |
|---|---|
| Janelas simultâneas | 4 por schema, com um dono |
| Adesão | 30 min; registro igual renova a mesma janela |
| Janela móvel | 1 s a 24 h; padrão 15 min |
| Pontos por resultado | Até 2.000; excesso falha sem resultado parcial |
| Payload | Até 4 MiB por janela |
| Descoberta compartilhada de publicações | A cada 2 s, lote de até 128 publicações |
| Atualização por relógio | Nas fronteiras relevantes, com reavaliação no máximo a cada 60 s |
| Leitura da trajetória | Timeout de 5 s, compartilhando as duas vagas já existentes |
| Atualizador sem checagem recente | Após 10 s, a leitura não anuncia o resultado como pronto |

A cadência de polling não promete reação instantânea. Um vencimento pode aguardar o próximo ciclo; nesse intervalo a API devolve 503 em vez de servir um resultado vencido como pronto. Os limites de payload não equivalem a um teto de memória total, disco ou SLA de latência.

## Publicação, janela e checkpoint

O atualizador lê a disponibilidade serializada das normalizações usando a barreira temporal já existente. Uma nova publicação é relacionada às janelas por fonte, chain, endpoint, horário recebido e identidade do token. Uma revisão vazia ainda encontra a identidade nos fatos históricos e invalida o resultado anterior.

Publicações irrelevantes não provocam leitura da trajetória daquela janela. Um lote acima do orçamento marca as janelas ativas para reconstrução limitada no corte atual, sem percorrer um backlog ilimitado. A projeção conserva o último estado conhecido; não afirma ter emitido um frame para cada revisão intermediária.

Vencimentos consideram saída de amostras pela borda esquerda inclusiva, frescor do último valor, resolução do gap inicial e a reavaliação de 60 s do relógio. A janela informa seu corte e suas fronteiras reais. Horários futuros recebidos em publicações novas são tratados como entradas futuras; a reavaliação limitada também reconcilia esse caso após reconstruções.

O resultado, a geração, o corte de publicações considerado e o próximo vencimento são gravados **juntos, antes de liberar o payload em memória**. Falha de cálculo, excesso, espera ou gravação deixa a janela pendente. Um resultado anterior persistido permanece recuperável, mas não é servido como pronto durante a falha.

Um lock consultivo de sessão, separado por schema, mantém um único dono. A gravação usa a mesma conexão que possui o lock. A retomada valida a configuração armazenada, recupera a geração e reconstrói a janela no corte atual antes de liberá-la. Perda da conexão ou do dono suspende as leituras e permite nova aquisição. Referências oficiais: [locks consultivos PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS), [Read Committed](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED) e [cancelamento asyncio](https://docs.python.org/3.13/library/asyncio-task.html#task-cancellation).

O checkpoint guarda **a última projeção**, não um ledger de decisões nem todas as gerações de frames. A consulta histórica continua reconstruindo os fatos no corte solicitado; o consumo efetivo por estratégias e seu histórico serão definidos nos recortes seguintes.

## API

Todas as rotas exigem a autenticação existente.

| Rota | Comportamento |
|---|---|
| POST /v1/feature-windows | Registra/renova configuração canônica; retorna 202 e window_id. Parâmetros: source_kind, chain, token, method, window_seconds e gap_after_seconds opcional. |
| GET /v1/feature-windows | Status em memória: configurações, gerações, próximos vencimentos, falhas, limites e contagens. |
| GET /v1/feature-windows/{window_id} | Payload imutável em memória, sem leitura SQL por consumidor. 503 enquanto pendente/vencido; 404 quando não registrado. |
| DELETE /v1/feature-windows/{window_id} | Remove somente a adesão e o último resultado derivado; capturas/fatos preservados. |

Configurações iguais compartilham window_id e estado. Registro não solicita dados ao coletor. Adesões expiradas deixam de ser atualizadas; a tabela mantém no máximo o pequeno conjunto de registros até a próxima limpeza no registro. Sem adesões, não há leitura de trajetórias ou varredura de fatos.

A resposta contém geração, motivo da atualização (`initial`, `publication`, `clock` ou `recovery`), corte considerado, vencimento e série/features com proveniência. `ready` significa pronto no corte declarado; não significa cobertura contínua de mercado ou ausência de publicações ainda não descobertas.

## Testes

**125 testes aprovados**, zero falhas, erros ou skips, na imagem Docker Python 3.13 candidata. São dez novos casos e 115 regressões temporais. [Unidades](tests/test_feature_windows.py) e [integração PostgreSQL](tests/test_feature_windows_integration.py).

O teste integrado utiliza schema privado, capturas sintéticas e o PostgreSQL real:

- 20 leitores concorrentes recebem os mesmos bytes; funcionam com o acesso ao pool de SQL pelo Store bloqueado no teste.
- Publicação de outro token não relê a janela; revisão, chegada atrasada e invalidação mudam os valores corretos.
- Replay conserva a publicação original e não dispara trabalho redundante.
- Falha induzida antes de gravar o checkpoint conserva o payload persistido e suspende a leitura até recuperar.
- Segundo dono recusado; desconexão/reconexão recupera geração/configuração antes da nova publicação.
- Estouro do lote reconstrói o estado limitado; limites de adesão, autenticação e consulta passam.
- O loop real avança uma geração por relógio sem leitor HTTP ou nova captura; prazos são encurtados somente na fixture.

A prova mede comportamento funcional. Não é teste de 20 estratégias, benchmark de capacidade ou teste de perda de energia.

## Prova real e retomada

Uma única adesão foi registrada sobre o token Solana já coletado `6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx`, método Trending, janela de 900 s e gap de 120 s.

**20 leituras HTTP concorrentes: zero leituras adicionais da trajetória.** Uma publicação real da coleta existente atualizou a janela automaticamente, com **15 observações**. O conteúdo foi comparado com a consulta histórica no mesmo corte e com o payload persistido.

Em seguida, somente a API foi reiniciada. A adesão foi recuperada sem novo POST; a resposta retornou com motivo `recovery` e geração posterior à observada antes do reinício. A conferência terminou em **2026-09-10T21:18:14.836172Z**. Três respostas históricas de features permaneceram exatamente iguais.

Adesão deixada ativa, sem renovação automática neste aceite:
- window_id: `29eab9f4e4952fbd479f80ebf3fe4ec2041a39485dc60d69ff1e4f15570a9d55`;
- expiração: **2026-09-10T21:45:33.563154Z** (18:45:33 em Brasília);
- não altera a frequência da coleta nem produz ordens ou mensagens.

API em `midas-data-app:feature-windows-v1-20260910`, SHA `1efc6eb4ddd229eb547c6058e4bb2f6223871cc118d964e0e2c1be4ea49d0ca1`. Os cinco outros containers mantiveram IDs/imagens; os seis ficaram saudáveis. A migração acrescenta uma tabela e um índice, sem reprocessar ou modificar fatos.

Evidências: [checkpoint-feature-windows-20260910](../.scratch/checkpoint-feature-windows-20260910/), [testes finais](../.scratch/checkpoint-feature-windows-20260910/contract-tests.xml), [aceite real](../.scratch/checkpoint-feature-windows-20260910/live-acceptance.json), [resultado recuperado](../.scratch/checkpoint-feature-windows-20260910/after-restart.json), [runtime](../.scratch/checkpoint-feature-windows-20260910/runtime-acceptance.json). Baselines, scripts, imagem e rodada inicial também estão preservados nesse diretório.

## Próximo passo

**5.2:** primeira regra versionada consumindo este resultado compartilhado, com estados true/false/unknown, dependências e motivo de avaliação/abstenção. Preservar a versão e o corte usados, sem confundir resultado de regra com operação financeira. Timers/regras específicos, posições paper e medição de escala continuam em seus recortes.
