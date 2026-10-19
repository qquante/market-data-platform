# Contrato mínimo de holders — 6.1.1

**Histórico da 6.1.1/6.1.2.** O contrato semântico v1 foi ampliado pelo [contrato HTTP v2](contracts/gmgn-holders-http-v2.json): parser, persistência, consultas e agenda implementados/testados. [Estado atual](FINAL_ACCEPTANCE.md): implantação e canário contínuo pendentes. O texto abaixo preserva a evidência inicial CP-HOLDERS-CONTRACT-01; não representa o status atual do código.

Contrato versionado: [gmgn-holders-v1.json](contracts/gmgn-holders-v1.json). Seu status atual é `offline_parser_implemented`; o arquivo não registra um endpoint no runtime.

## Recorte e origem

Endpoint `token_top_holders`, GET `/v1/market/token_top_holders`. O [cliente oficial](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/src/client/OpenApiClient.ts) confirma rota e autenticação de consulta. A [definição do comando](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/src/commands/token.ts) declara limite padrão 20, máximo 100, ordem e filtro de tag. Todas as fontes foram fixadas no commit `ec95135ecabfdf617c0690a28af64e84d22d5210`, com hashes no JSON.

Cada lista pertence a uma chamada para um token, com `chain/address/limit/order_by/direction/tag` preservados. Ausência de tag significa ausência desse filtro explícito. A lista continua parcial; número de linhas não é total de holders. Não há contrato de paginação homologado neste recorte. Parâmetros ausentes no legado permanecem desconhecidos; defaults atuais não reconstroem a requisição antiga.

## Identidade e campos

Grão proposto: **uma linha de holder dentro de uma captura**, identificada por `capture_id + contract_version + row_pointer`. Dimensões: cadeia, token consultado, carteira e token account quando presente. A carteira vem de `list[i].address`; o token vem do parâmetro da chamada. Endereços Solana conservam maiúsculas/minúsculas.

O modelo atual [Fact](midas_data/contracts.py) só tem identidade de token. O parser offline retorna `HolderObservation` separado; não coloca carteiras em `Fact.token_address` nem transforma vários saldos de carteiras em uma série ambígua do token. Persistência e consultas ficam para um recorte posterior.

Os sete caminhos abaixo têm semântica documentada no [referencial oficial de resposta](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/skills/gmgn-token/SKILL.md#token-holders--token-traders--response-fields). Esta revisão aplica o contrato somente a holders/Solana; não homologa o endpoint de traders.

| Campo em `list[i]` | Significado no contrato | Regra |
|---|---|---|
| `address` | Carteira reportada | String obrigatória, não vazia, até 256 bytes UTF-8; não prova existência/controle on-chain. |
| `account_address` | Token account reportada | Opcional; distinta da carteira, sem inferir owner/mint ou substituir endereço ausente. |
| `balance` | Saldo do token em unidade humana | Decimal finito ≥ 0; não reaplicar decimals. Fonte principal de `wallet_token_balance`. |
| `amount_cur` | Alias documentado do saldo | Preservar separado; não somar nem usar como fallback silencioso. Divergência bloqueia a interpretação unificada. |
| `amount_percentage` | Participação reportada no supply total | Fração entre 0 e 1; denominador/metodologia do provedor não verificados independentemente. |
| `native_balance` | Saldo nativo da carteira | Em Solana, inteiro exato em lamports; não é saldo do token consultado. Outras cadeias pendentes. |
| `usd_value` | Valor em USD reportado | Decimal finito ≥ 0; não serve como preço executável. |

Parse com Decimal a partir dos bytes, sem float intermediário. JSON numérico ou texto numérico estrito; booleanos, NaN/infinito e valores fora do domínio são inválidos. Chave ausente não cria zero; null/texto vazio ficam missing. Cada valor conserva pointer, tipo, unidade, quality e reason. `valid` significa que atende ao contrato do provedor, sem certificação externa.

Conflito `balance/amount_cur` conserva ambos e emite `balance_alias_conflict`, deixando o saldo unificado unverified. Carteira inválida impede a linha semântica, mas conserva bruto e issue. Carteira repetida conserva as linhas e sinaliza ambiguidade; não deduplica nem soma implicitamente. Campos extras permanecem no bruto/catálogo.

## Tempo, fidelidade e limites

UTC/GMT+0 é canônico. A captura futura reutiliza os metadados existentes, com clocks de transporte explícitos; publicação registra `available_at` no destino. Sem instante de snapshot comprovado, `source_event_at` fica null e `received_at` é somente proxy declarado. Criação da carteira, última atividade e início de holding são atributos, não clocks do snapshot.

As amostras existentes são **stdout JSON de CLI**, não bytes HTTP originais. Os horários do manifesto delimitam o processo CLI e não provam os instantes exatos de request/receive HTTP. Importação eventual deve declarar `legacy_reserialized` e preservar essa limitação; não retroagir disponibilidade. O [cliente oficial](https://github.com/GMGNAI/gmgn-skills/blob/ec95135ecabfdf617c0690a28af64e84d22d5210/src/client/OpenApiClient.ts) faz parse JSON e devolve o objeto de dados; precisão anterior à serialização CLI não pode ser recuperada a partir destes arquivos.

O parser 6.1.2 mantém os limites de 8 MiB, 20.000 nós JSON e profundidade 32, com no máximo 100 linhas e 16 MiB de saída JSON. Excedente é erro explícito, sem truncamento silencioso. A sugestão para uma coleta posterior é 20 linhas por chamada, ordem `amount_percentage desc`, sem filtro de tag, inicialmente Solana. Nada foi habilitado. Peso documentado 5 não é medição da quota disponível nem promessa de frescor; integração posterior reutiliza a agenda e o orçamento existentes.

## Evidência inicial — 6.1.1

Origem: `reports/gmgn-5dvXTZ5q-20260910/raw/`, token Solana `5dvXTZ5qwgafnHtwu3Ls3QrWx1U4LQsFeCuJgkk4QEC6`. Seleção pequena, sem executar os scripts de coleta:

| Consulta arquivada | Resultado conferido |
|---|---|
| amount_percentage / desc / sem tag / 100 | 100 linhas |
| amount_percentage / desc / dev / 100 | 1 linha |
| profit / desc / sem tag / 100 | 100 linhas |
| profit / desc / sniper / 100 | Sucesso com lista vazia |
| amount_percentage / asc / sem tag / 100 | CLI falhou, exit 3221226505; arquivo de zero bytes, não lista vazia |

**Cinco arquivos, quatro JSONs válidos, 201 observações de linhas em momentos distintos.** Não são 201 carteiras únicas. As linhas não vazias contêm os sete campos; saldos/unidades representadas e domínio da fração passaram. `balance == amount_cur` nos 201 casos observados não prova igualdade universal.

Os cinco hashes de stdout conferem com o manifesto após normalização CRLF→LF. Nos quatro JSONs, o hash físico do arquivo difere por uma quebra CRLF final; os dois hashes são registrados, sem reescrever a origem.

Quatro chamadas offline ao normalizador atual, usando os corpos salvos e **metadados fixture explícitos**, retornaram zero fatos e `unsupported_endpoint`. O catálogo preservou respectivamente 8.844, 92, 8.864 e 2 nós. Isso verifica a fronteira atual; não é teste de um novo adapter nem ingestão real. Nenhuma suíte de runtime precisou ser ampliada para esta entrega documental.

Evidências e reprodução: [.scratch/checkpoint-holders-contract-20260910](../.scratch/checkpoint-holders-contract-20260910), com `evidence_audit.json`, `audit_evidence.py`, fontes oficiais, backups e hashes. Execute da raiz: `.venv/Scripts/python.exe -X utf8 .scratch/checkpoint-holders-contract-20260910/audit_evidence.py`.

## O que fica para os próximos recortes

- **6.1.2 concluída:** modelo/parser offline separado, amostras salvas e fixtures de identidade, ausência, repetição, alias, precisão e limites. [Resultados: 343 testes](HOLDERS_PARSER.md). Sem SQL ou coleta.
- **6.1.3:** persistência e consulta limitada por captura/token/carteira, preservando clocks, revisão e proveniência; homologar envelope HTTP antes de conectar coleta.
- **6.1.4:** canário pequeno na agenda existente, quota, frescor e retomada. Só então ampliar acompanhamento.
- Total de holders (P0-45), top10 derivado (P0-48), P&L, tags, funding, social, traders/dev/pools e outras cadeias continuam fora deste contrato. Nenhuma distribuição completa, score de risco ou vínculo entre pessoas é inferido.

Validação final às 02:30:26Z de 11/09: JSON e links locais conferidos; 101 arquivos de código/SQL/Compose mantiveram os hashes e os seis containers mantiveram IDs/imagens e saúde. Registro em `validation.json` no diretório de evidências.

A 6.1.1 alterou documentação e catálogo. A implementação offline posterior está descrita na [6.1.2](HOLDERS_PARSER.md); schema SQL, serviços e coleta continuam na versão anterior. A etapa 6.1 não está concluída.
