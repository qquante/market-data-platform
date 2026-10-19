# Painel operacional e de pesquisa

> Atualização CP-DASHBOARD-ALL-05: [dashboard completo dos dados disponíveis, validação e limites](DASHBOARD_ALL_DATA.md).

> Atualização CP-RESEARCH-QUALIFICATION-03: [entrega atual, evidências e limites](RESEARCH_QUALIFICATION.md). Qualificação empírica, amostra 300/frame 5.438, OHLCV, fila/cache e carga real implantados; estratégia ainda sem validação por cobertura insuficiente. O aceite anterior abaixo permanece histórico.

> Estado vigente em 11/09/2026: implantado e homologado na base real, CP-RESEARCH-UI-LIVE-02. [Aceite live, comandos e limites](LIVE_ACCEPTANCE.md). As referências abaixo a implantação pendente/preview registram o checkpoint local anterior.

Checkpoint CP-RESEARCH-UI-01, 11/09/2026. Interface empacotada na API: HTML/CSS/JS estáticos, sem framework/build separado. Aceite visual e funcional realizado em servidor fixture isolado na porta 8794; a API real na 8791 continua na imagem anterior até autorização de implantação.

## Operação — 7.1

Mostra processo do coletor, frescor, fila de entrega, admissão por armazenamento livre, filas/idade/falhas por destino, heartbeat das etapas, tokens acompanhados e cooldown. Recursos da API e do worker vêm do cgroup: RAM atual/limite e consumo de CPU entre amostras. Falta de medida aparece como indisponível; não é zero. 100% de CPU representa um núcleo.

Atualização operacional a cada 10 s somente enquanto a aba está visível; pedidos não se sobrepõem. Erro mantém o aviso da amostra anterior. É uma visão do estado medido, não garantia de latência. A agenda de holders publica somente 20 jobs no heartbeat, com total/estados/has_more, para preservar o limite de 64 KiB.

## Trajetórias e decisões — 7.2

Consulta token, origem, dia UTC, método de valuation e corte de conhecimento. SVG preserva gaps, valores inválidos e ambiguidades; não conecta uma lacuna como se fosse uma cotação. Clique em um ponto revela valor exato, clocks, captura/hash e proveniência. Números arredondados servem apenas ao desenho.

Marcos são intervalos entre amostras. Não são timestamps exatos do mercado nem primeira passagem global. Holders respeitam fonte/corte e mostram que a lista é parcial. As posições/decisões paper são do estado atual da base, explicitamente fora do corte histórico da curva. O painel só consulta essas posições; não envia ordens.

## Pesquisa e credencial

Lista jobs/tentativas, inclusive falhas e censura; permite submissão explícita de definição JSON, inspeção e exportação com SHA-256 conferido no navegador. A interface usa apenas rotas locais da API.

No Compose local, DATA_DASHBOARD_PUBLIC_READ=1 libera GET/HEAD em /v1/ para localhost, 127.0.0.1 e ::1; o painel carrega automaticamente sem chave. A porta permanece publicada apenas em 127.0.0.1. Host externo e requisições de navegador cross-site continuam autenticados. DATA_DASHBOARD_PUBLIC_READ=0 restaura autenticação das consultas; fora do Compose o padrão é desabilitado. POST/PUT/PATCH/DELETE, /docs e /openapi.json continuam exigindo Bearer. Autorizar alterações é opcional e habilita a criação de coortes/experimentos. A chave fica somente em memória na página, nunca em URL/localStorage; recursos estáticos e /ui/config nunca contêm a chave. CSP self, frame-ancestors none, no-store, nosniff e no-referrer. Conteúdo da fonte é inserido com textContent. Sair apaga a chave e cancela solicitações; com leitura local habilitada, as consultas permanecem liberadas. Recarregar a página remove-os.

## Homologação executada

Chromium headless, 1440×1050 e 390×844, com SQL privado + HTTP real local. Conexão, quatro cartões, 29 pontos/3 segmentos, detalhe de captura, criação de coorte, download com hash e encerramento de sessão aprovados. Zero erros de página e nenhuma rolagem horizontal indevida no celular. Capturas desktop/mobile inspecionadas.

Artefatos: .scratch/checkpoint-dashboard-20260911/browser-acceptance.json e PNGs operations-desktop, trajectory-desktop, trajectory-mobile, research-desktop. O banner da prévia informa explicitamente fixture. Essa homologação não prova que o painel já esteja implantado ou recebendo holders contínuos reais.

Harnês tests/preview_server.py exige DATA_INTEGRATION=1, cria somente ui_fixture_<uuid>, usa raw em /tmp e exclui apenas esse schema no encerramento gracioso. Não inicia provedor, analytics ou operação financeira. O acesso pelo host precisa de rede ingress na prévia, pois a rede data é interna. Não reutilizar o schema de produção como fixture.

A prévia temporária foi encerrada após o aceite; a porta 8794 não é serviço permanente. Screenshots e artefatos permanecem disponíveis. O relatório acceptance-summary.json registra limpeza dos schemas e hashes da imagem.

Correção do harnês: Uvicorn reemite SIGTERM após o lifespan; a limpeza agora ocorre dentro do lifespan, antes desse sinal, e grava preview-cleanup.json. A primeira versão deixou três schemas fixture, removidos após verificação explícita. O novo ciclo start/stop e a ausência dos schemas foram comprovados em leitura.

## Acesso local sem chave — CP-DASHBOARD-NO-KEY-06

Implantado em 11/09/2026, imagem `midas-data-app:dashboard-no-key-v1-20260911`. Aceite na base real: abertura direta de `#tokens`, recarga, seis áreas, paginação e downloads; 16 verificações aprovadas, zero erros JS/5xx e zero cabeçalhos Authorization enviados. Escritas sem chave retornam 401. Testes empacotados: 19 aprovados, 1 integração não executada; não foi necessário alterar schema. API e worker saudáveis, seis ledgers paper e estado de atividade preservados; coletor e outros serviços não reiniciados. Evidências em `.scratch/dashboard-no-key-20260911/`.
