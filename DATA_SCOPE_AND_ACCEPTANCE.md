# Abrangência dos dados antes das estratégias — CP-DATA-SCOPE-09

> **Atualização CP-DATA-SPEED-12:** [otimização implantada e medida](SPEED_RESULTS.md). Perfil ativo 8 s / 16 s, painel 2 s; teste curto aprovado, histórico ~26 s. 5 s / 10 s ainda acumula fila. Três capturas NUL em quarentena; cobertura ampla continua aberta. Os valores anteriores abaixo são históricos.

> Atualização CP-DATA-CONTINUOUS-11: [agenda contínua implantada](CONTINUOUS_COLLECTION.md), sem teto artificial, alimentada por Trenches/Trending e pelo cadastro anterior. Retomada da fonte e reinício provados; primeira cobertura individual segue parcial. Um campo NUL permanece em quarentena. D1–D5 continuam sujeitos ao aceite amplo.

11/09/2026. Diretriz reafirmada pelo usuário: capturar os dados de mercado do projeto, categorizar corretamente, preservar o histórico e provar cobertura/armazenamento antes de escolher estratégias. **O aceite CP-BASE-FIELDS-08 fecha somente o catálogo de uma amostra das cinco fontes ativas. Não fecha esta etapa ampla de dados.**

## Sentido do projeto

Construir um histórico longitudinal de tokens que permita reconstruir o que era observável antes, durante e depois de cada evento/nível, comparar trajetórias e desfechos e, posteriormente, descobrir e testar estratégias. A população precisa conservar tokens que avançam, caem, param de negociar ou perdem cobertura. O resultado não deve depender de coletar apenas as variáveis exigidas por uma estratégia previamente escolhida.

Exemplo de pergunta: no primeiro instante observado acima de um nível da grade, quais eram preço, supply, liquidez, fluxo, concentração, criador, riscos e atenção? O que mudou depois, em quanto tempo, e quantos tokens comparáveis tiveram outro desfecho? Cruzamento observado, inferido e perdido entre consultas permanecem distintos.

## Três coberturas diferentes

1. **Campos:** tudo que foi recebido deve permanecer no bruto e no inventário; um caminho conhecido não homologa todas as suas ocorrências.
2. **Entidades:** registrar quem entrou no universo e quem foi acompanhado; top N não equivale a toda a população.
3. **Tempo:** informar quando e por quanto tempo cada entidade/família foi observada; cadastro permanente não é acompanhamento contínuo.

A meta é cobertura explícita dos dados acessíveis da GMGN para o universo definido. Não se promete todo evento de toda cadeia, carteira ou período que a própria fonte não expõe. Limite da fonte, falha nossa e dado ainda sem significado homologado precisam de estados separados.

## Famílias que o fechamento amplo deve contemplar

| Família | Conteúdo a preservar e organizar | Situação da base nova |
|---|---|---|
| Descoberta e universo | Origem, filtros, cadeia, token, primeira observação, ranking/posição, estágios de lançamento, entrada/saída da seleção | Ranking e Trenches alimentam a agenda persistente v2; cadastro anterior importado. Cobertura individual parcial |
| Identidade e ciclo de vida | Nome/símbolo, endereços por papel, criação/abertura/migração, launchpad, ativo de cotação | Parte presente em snapshots; não equivale a ciclo completo de cada token |
| Mercado, valuation e pools | Preço, supply por tipo, MC reportado/derivado, liquidez, reservas e taxas, pool e moedas correspondentes | token_info/ranking/Trenches ativos; pool_info dedicado ausente na agenda |
| Histórico e fluxo | OHLCV, volumes, compras/vendas/swaps, janelas, barras completas, lacunas | Kline e contadores presentes; escopo temporal e populacional limitados |
| Segurança e risco | Autoridades, renúncias, flags/rótulos, taxas e riscos reportados; validade por cadeia | Campos embutidos presentes; token_security dedicado ausente na agenda |
| Holders e concentração | Carteira/conta, saldo, participação, rótulos, concentração e evolução | Top holders ativo em listas parciais dos tokens admitidos; parte sem projeção tipada |
| Traders e atividade de carteiras | Lista de traders, compras/vendas/transferências, custos e resultados reportados, períodos e paginação | Coleta dedicada de traders/atividade ausente na base nova; top traders não é log completo de trades |
| Criadores e vínculos | Criador, origem de fundos reportada, tokens criados, histórico de criação, carteiras relacionadas quando a fonte sustenta o vínculo | Metadados embutidos; expansão de created_tokens e atividade dedicada pendente |
| Atenção e contexto social | Links/perfis, alterações, contadores, buscas/sinais, feeds públicos KOL/smartmoney | Parte dos metadados embutidos; feeds dedicados não ativos. Links não constituem arquivo de posts do X |

A etapa atual opera Solana. Cada cadeia adicional precisa de uma linha própria na matriz endpoint/cadeia, com identidade, unidade e aplicabilidade verificadas. Não considerar suporte global da GMGN como prova de captura ou contrato validado em cada cadeia. Não habilitar novas cadeias ou rotas financeiras por inferência desta matriz.

Referências oficiais consultadas em 11/09/2026: [token](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md), [portfolio](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-portfolio/SKILL.md) e [track](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-track/SKILL.md). Esta consulta confirma famílias documentadas; não é teste autenticado de todas as rotas nem autorização para aumentar quota, operar carteiras ou obter conteúdo de outra plataforma.

## Pendências concretas, em ordem

- [ ] **D1 — Matriz de cobertura por fonte:** conciliar catálogo amplo de endpoints com as cinco rotas da agenda; declarar o que é dado de mercado, identidade, histórico, contexto, conta pessoal ou execução. Para cada família: cadeia, entidades, campos, janela/histórico acessível, filtros, paginação/limite, frequência pretendida/concedida, peso/quota e limitações. Não esperar definição de uma estratégia para inventariar as famílias.
- [ ] **D2 — Universo e continuidade (base implantada em CP-DATA-CONTINUOUS-11; aceite temporal amplo aberto):** retirar a dependência exclusiva de candidatos do top Trending para a população de pesquisa; admitir a descoberta relevante de Trenches e demais origens com proveniência. Acompanhar fora do ranking pelo horizonte de observação declarado. Persistir admissões, adiamentos, interrupções e perda de cobertura. A concessão de 30 min foi removida; ainda falta comprovar dias de cobertura e suas interrupções, sem confundir permanência na agenda com resposta atual.
- [ ] **D3 — Completar aquisição e interpretação:** integrar as famílias de leitura faltantes por fatias, reutilizando fila/quota/raw; revisar formatos opcionais e históricos. Fechar os contratos documentados ainda sem adapter. Para ambiguidades e campos opacos, registrar exatamente o limite da fonte e bloquear somente usos sem suporte; não rotular desconhecimento como dado limpo.
- [ ] **D4 — Preservação e recuperação atuais:** manter original imutável, metadados, UTC, valores exatos, versões e IDs de ligação. Conferir bruto → normalização → destinos, replay sem duplicação e retenção. Um novo aceite de recuperação requer backup/restauração da base atual; essa prova foi dispensada após reset e não existe neste checkpoint. Não reaproveitar o script antigo fixado em outro projeto sem adaptação.
- [ ] **D5 — Aceite operacional de dados:** verificar a matriz completa implementada em operação, cobertura por token/família/tempo, cadência concedida, quota, períodos ausentes, falhas, crescimento e recuperação. Teste proporcional ao perfil real; sem nova stack ou benchmark genérico. Só então encerrar a etapa ampla e passar à construção de datasets/estratégias.

## Limitação atual comprovada

CP-DATA-CONTINUOUS-11 retirou o teto de 10/20 e a concessão de 30 minutos, importou o cadastro inteiro disponível e comprovou retomada. Cadastro e primeira aquisição de cada família continuam diferentes: dezenas de milhares de consultas individuais permanecem pendentes, sob quota e prioridade. Os alvos de 10 min/6 h não são garantia universal; fonte atual e processo saudável não certificam todas as entidades atualizadas.

Os 831 caminhos do dicionário são uma baseline de campos (805 observados em 200 capturas e 26 previstos). Os 309 bloqueados têm causas diferentes: 115 descritos sem projeção homologada, 53 contratos com ressalvas, 140 somente observados e 1 conflito documental. Dois caminhos opcionais novos de tradução em near_completion já apareceram depois. A manutenção deste catálogo é parte de D3, não substitui D1/D2/D4/D5.

## Critério de pronto

Para cada família incluída no universo: sabemos o que a fonte oferece; a captura está ligada ou há impedimento explícito; os dados recebidos são preservados; cada campo tem interpretação comprovada ou incerteza classificada; as relações e relógios são corretos; a cobertura observada e as lacunas são consultáveis; valores elegíveis podem ser reconstruídos sem informação futura; entrega/recuperação foram exercitadas no ambiente vigente. Contagem de campos e de testes, isoladamente, não encerra esse aceite.

O checkpoint de origem foi uma revisão de escopo. CP-DATA-CONTINUOUS-11 implementou a base de D2 e corrigiu gargalos encontrados na implantação; o aceite amplo D1–D5 permanece aberto. Não houve ativação de estratégias, tarefa separada ou automação nesta fatia.
