# Empório da Música — Agente de Atendimento

Protótipo de agente de atendimento por mensagens de texto para a **Empório da Música**, loja (fictícia) de instrumentos musicais em Campo Grande/MS. Desenvolvido para o Desafio Técnico de AI Engineer.

O agente usa **Claude (Anthropic)** com *function calling*. A cada mensagem, o próprio modelo decide se precisa consultar **dados operacionais** (catálogo, estoque, promoções, pedidos, num banco SQLite) ou **políticas da loja** (horários, trocas, pagamento, frete, no manual em PDF processado por seções). Ele responde com a persona definida pelo manual: um amigo que entende de música.

```text
Cliente: Quanto custa o Takamine GD20?
  └─ agente chama detalhes_produto {"nome": "Takamine GD20"}  → R$ 2.199,00 · PIX R$ 2.089,05 · 12x R$ 183,25
Empório: O Takamine GD20 sai por R$ 2.199,00 ...
```

## Sumário

1. [Como rodar](#como-rodar)
2. [Exemplos de interação](#exemplos-de-interação)
3. [Arquitetura](#arquitetura)
4. [Decisões técnicas e justificativas](#decisões-técnicas-e-justificativas)
5. [Tratamento dos dados](#tratamento-dos-dados)
6. [Manual de políticas (retrieval)](#manual-de-políticas-retrieval)
7. [Ferramentas do agente](#ferramentas-do-agente)
8. [Agente: prompt de sistema e tool calling](#agente-prompt-de-sistema-e-tool-calling)
9. [Interface CLI e histórico](#interface-cli-e-histórico)
10. [Testes](#testes)
11. [Suposições](#suposições)
12. [Limitações conhecidas e próximos passos](#limitações-conhecidas-e-próximos-passos)
13. [Uso de assistentes de código](#uso-de-assistentes-de-código)
14. [Estrutura do projeto](#estrutura-do-projeto)

## Como rodar

**Requisitos:**

- [uv](https://docs.astral.sh/uv/), que gerencia o Python e as dependências.
- Python 3.11+ (o `uv` instala automaticamente se necessário).
```bash
# 1. Instalar dependências (cria o ambiente virtual em .venv)
uv sync

# 2. Configurar variáveis de ambiente
cp .env.example .env      # no Windows (PowerShell): Copy-Item .env.example .env
# edite o .env e preencha ANTHROPIC_API_KEY

# 3. Conversar com o agente no terminal
uv run emporio-chat
uv run emporio-chat --debug    # mostra as ferramentas chamadas a cada resposta

# 4. Rodar os testes (não chamam a API: usam um cliente falso)
uv run pytest

# 5. (opcional) Gerar novamente as conversas de exemplo (chamam a API)
uv run python examples/generate_examples.py

# 6. (opcional) Ver o relatório de qualidade dos dados e o manual processado
uv run python -m emporio_agent.data.loader
uv run python -m emporio_agent.policies.manual
```

O banco SQLite é criado automaticamente na primeira execução e reconstruído quando algum CSV muda.

**Variáveis de ambiente** (todas em [`.env.example`](.env.example)):

| Variável | Padrão | Para que serve |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chave da API (obrigatória) |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Modelo usado pelo agente; pode ser trocado sem mexer no código |
| `ANTHROPIC_MAX_TOKENS` | `1024` | Limite de tokens por resposta |
| `DATA_DIR` / `STORAGE_DIR` | `data` / `storage` | Pasta dos dados de entrada / do banco gerado |
| `STORE_TIMEZONE` | `America/Campo_Grande` | Fuso da loja (horário de funcionamento e prazos) |
| `STORE_TODAY` | vazio (data real) | Fixa a data (`AAAA-MM-DD`) ou data e hora (`AAAA-MM-DDTHH:MM`) para reproduzir cenários |
| `PARTNER_STORES` | vazio | Lojas parceiras indicadas quando pedem acessórios, separadas por `;` |

## Exemplos de interação

As conversas ficam em [`examples/`](examples/). São transcrições de execuções reais, geradas por script e sem edição manual, que mostram as ferramentas chamadas em cada turno:

| Arquivo | Cenário |
|---|---|
| [`01_violoes_ate_1000.md`](examples/01_violoes_ate_1000.md) | Violões disponíveis até R\$ 1.000 (busca com teto de preço) |
| [`02_endereco_horario.md`](examples/02_endereco_horario.md) | Endereço da loja e horário de "amanhã" |
| [`03_preco_takamine_gd20.md`](examples/03_preco_takamine_gd20.md) | Preço do Takamine GD20, promoção expirada, PIX e parcelamento |
| [`04_arrependimento_devolucao.md`](examples/04_arrependimento_devolucao.md) | **Caso não trivial:** arrependimento, com verificação do pedido e prazo calculado |
| [`05_acessorios_fora_do_escopo.md`](examples/05_acessorios_fora_do_escopo.md) | Pedido de cordas, cabo e pedal: recusa educada e retomada |

Detalhes sobre como são gerados (data simulada, reprodutibilidade) estão em [`examples/README.md`](examples/README.md).

## Arquitetura

```text
 Cliente (terminal)
        │ mensagem
        ▼
 interface/cli.py ──► agent/session.py (ChatSession: histórico em memória)
                              │ histórico + mensagem
                              ▼
                      agent/core.py (loop de tool calling) ◄──► Claude (Messages API)
                              │  tool_use / tool_result         system: agent/prompt.py
                              ▼
                      tools/ (ToolExecutor: valida com Pydantic e executa)
            ┌─────────────────┼──────────────────────┬────────────────────┐
     buscar_produtos   detalhes_produto       consultar_pedido     consultar_politicas
            └──────── SQLite (somente leitura) ───────┘                    │
                 storage/emporio.db ◄── data/loader.py ◄── data/*.csv      │
                                                                           ▼
                                        policies/manual.py ◄── data/políticas_da_loja.pdf
```

Fluxo de uma mensagem:

1. O CLI repassa a mensagem à `ChatSession`, que junta o histórico da conversa.
2. O agente envia histórico, prompt de sistema e definições das ferramentas ao Claude.
3. O modelo responde em texto, ou pede uma ou mais ferramentas (`tool_use`).
4. As ferramentas são executadas em Python sobre o SQLite ou o manual; os resultados voltam ao modelo como `tool_result`, e o ciclo se repete até a resposta final.
5. A resposta é exibida e a troca completa, incluindo as ferramentas, entra no histórico.

## Decisões técnicas e justificativas

| Decisão | Escolha | Por quê |
|---|---|---|
| **Abordagem do agente** | *Function calling* com ferramentas determinísticas: SQL para dados, seções do manual para políticas | As perguntas da loja são de dois tipos bem distintos: **dados exatos** (preço, estoque, status) e **regras** (trocas, horário). Ferramentas dedicadas deixam o modelo escolher a fonte e garantem que número nenhum venha da "memória" do LLM. |
| **Dados operacionais** | SQLite + ferramentas com filtros, não RAG nem agente text-to-SQL | "Violão até R\$ 1.000 com estoque" é um filtro exato; busca vetorial é aproximada. Text-to-SQL livre daria ao modelo poder para errar a regra de preço ou expor dados de clientes; com ferramentas fechadas, as regras de negócio e a privacidade ficam no código. |
| **Retrieval do manual** | Divisão pelas seções numeradas do próprio PDF; busca por tópico ou TF-IDF | O manual tem cerca de 15 mil caracteres e 10 seções. Embeddings + banco vetorial não trariam ganho nesse tamanho, e dividir por seção mantém cada regra inteira e citável (§4.1). |
| **Modelo e provedor** | Claude Sonnet 5.5 (Anthropic), configurável por `ANTHROPIC_MODEL` | Bom equilíbrio entre seguir instruções com várias regras simultâneas (PIX × promoção, escopo, LGPD), *tool use* confiável (inclusive chamadas em paralelo) e português natural, com custo e latência menores que os do Opus. O projeto começou com o Sonnet 4.5, que deixou de estar disponível para a conta usada; a troca foi só de configuração. |
| **Framework** | SDK oficial `anthropic`, sem LangChain/LangGraph | Um agente, 4 ferramentas, um loop: o framework traria dependências e camadas de adaptação sem ganho. O loop próprio tem poucas dezenas de linhas, é explícito e é testado com um cliente falso. |
| **Estratégia de prompt** | Prompt de sistema com persona, regras invioláveis e um mapa de quando usar cada ferramenta; **cálculos no código** | O LLM erra contas e datas; por isso preço PIX, parcelamento e prazos de troca vêm prontos das ferramentas, e o prompt proíbe recalcular. Detalhes [abaixo](#agente-prompt-de-sistema-e-tool-calling). |
| **Interface** | CLI no terminal | O desafio foca no agente; o CLI é a forma mais rápida de testar e demonstrar. O agente (`ChatSession`) é independente da interface. |
| **Histórico** | Em memória, por sessão do CLI | Necessário para perguntas de acompanhamento ("E no PIX?"). A persistência entre execuções não agregaria a um protótipo de terminal de um único usuário (ver [limitações](#limitações-conhecidas-e-próximos-passos)). |
| **Tratamento dos dados** | Validação na carga, normalização para busca, regras de preço numa view SQL, inconsistências registradas | Ver [seção abaixo](#tratamento-dos-dados). |

## Tratamento dos dados

Os CSVs são carregados em um banco **SQLite** (`storage/emporio.db`), gerado por [`loader.py`](src/emporio_agent/data/loader.py) com o schema em [`schema.sql`](src/emporio_agent/data/schema.sql).

**Tratamentos aplicados:**

- Tipagem (preço → `REAL`, estoque → `INTEGER`), `CHECK` nos status e chaves estrangeiras validadas na carga.
- Campo `specs` (JSON) validado e incluído na busca.
- `search_text`: nome + categoria + descrição + specs em minúsculas e sem acento, para que "violao" encontre "Violão".
- Telefone só com dígitos e e-mail em minúsculas, para identificar o cliente independentemente do formato digitado.
- O banco é **reconstruído automaticamente** quando algum CSV muda, e as ferramentas do agente usam uma conexão **somente leitura**.

**Regras de negócio centralizadas na view `product_catalog`:**

| Regra | Origem |
|---|---|
| Disponível para venda = `status = 'active'` **e** `estoque > 0` | Manual §7.3 ("nunca confirmar disponibilidade sem verificar o estoque") |
| Só valem promoções com `is_active = 1`; havendo mais de uma, vale o maior desconto | Manual §7.3 ("nunca prometer desconto que não está vigente") |
| Preço PIX (−5%) só existe quando **não** há promoção ativa | Manual §6.2 (promoções não são cumulativas) |

**Inconsistências encontradas e como foram tratadas** (também ficam registradas na tabela `data_quality_issues`):

| Dado | Problema | Decisão |
|---|---|---|
| Produto 96 (Giannini GF-3D) | `active`, mas estoque 0 | Indisponível; o agente sugere alternativas |
| Produto 130 (Kala Barítono) | `coming_soon` com estoque 2 | Indisponível (lançamento ainda não liberado) |
| Produto 136 (Ibanez Bass 2X) | `discontinued` com estoque 13 | Indisponível (fora de catálogo) |
| Pedidos 3 e 20 | Total ≠ soma dos itens a preço atual | Vale o total registrado (o preço pode ter mudado ou havia desconto na época) |
| E-mail `bruno.martins@jmail.com` | Compartilhado por 2 clientes | O e-mail sozinho não basta para identificar o cliente |

## Manual de políticas (retrieval)

O PDF é processado por [`manual.py`](src/emporio_agent/policies/manual.py).

**Estratégia: divisão por seção do próprio documento, sem RAG vetorial.**

- O manual é curto (cerca de 15 mil caracteres) e já vem organizado em 10 seções numeradas (`1.`, `4.1`, ...). Dividir pela própria numeração mantém cada regra completa. Por exemplo, todo o §4.1 (arrependimento) fica num único trecho, o que blocos de tamanho fixo poderiam quebrar ao meio.
- Uma busca vetorial exigiria um modelo de embeddings (a Anthropic não oferece um) e um banco vetorial, e não traria ganho real para um documento desse tamanho.
- O resultado é determinístico e auditável: o agente pode citar "§4.1 Direito de Arrependimento".

**Como funciona a extração:**

- O `pypdf` em modo `layout` preserva a posição do texto. Isso permite detectar os títulos (`N.` e `N.N` na coluna 0) e as colunas das tabelas, que viram `Coluna | Coluna`.
- Cabeçalhos e rodapés de página são removidos e os bullets quebrados em várias linhas são reunidos. Listas que continuam na página seguinte (§7.2) ficam inteiras.

**Duas formas de consulta, expostas ao agente pela ferramenta `consultar_politicas`:**

1. **Por tópico** (`horario`, `pagamento`, `trocas_devolucoes`, `frete_entrega`, `promocoes`, `garantia`, ...): devolve a seção inteira com as subseções. O tópico é localizado pelo **título** da seção, não pelo número, para resistir a renumerações do manual.
2. **Por texto livre:** busca por palavras-chave (TF-IDF com normalização de acentos e plurais) nas subseções, para perguntas que não se encaixam num tópico.

**Inconsistências do manual** (as decisões abaixo estão fixadas no prompt de sistema):

| Ponto | Conflito | Decisão |
|---|---|---|
| Telefone / WhatsApp | §1.2: (67) 3341-4444; §7: (67) 3321-4500 | 3341-4444 como telefone da loja e 3321-4500 como WhatsApp de atendimento (o §7 trata especificamente do canal WhatsApp) |
| E-mail | Capa: `contato@emporiodamusica.com.br`; §1.2: `contato@empóriodamusica.com.br` | Usar a versão sem acento (domínios com acento são incomuns; o acento parece erro de digitação) |
| Parcelamento | Tabela §3: "parcela mínima R\$ 100"; §3.1: faixas de R\$ 50 / R\$ 80 / R\$ 100 | Vale o §3.1, que é mais específico |

## Ferramentas do agente

As ferramentas ficam em [`tools/`](src/emporio_agent/tools/). Cada uma tem nome e descrição (o que o modelo lê para decidir quando usá-la), um modelo Pydantic que gera o JSON Schema e **valida os argumentos** antes da execução, e uma função Python determinística.

| Ferramenta | Quando o modelo usa | Regras aplicadas no código |
|---|---|---|
| `buscar_produtos` | Perguntas abertas: marca, categoria, faixa de preço ("violão até R\$ 1.000") | Só itens disponíveis por padrão; indisponíveis correspondentes vêm separados |
| `detalhes_produto` | Um modelo específico: preço, PIX, parcelamento, estoque, especificações | Promoção × PIX não cumulativos; parcelamento do §3.1; alternativas se indisponível (§7.3) |
| `consultar_pedido` | Status, rastreio, troca, devolução, garantia | Verificação de identidade (LGPD); prazos de arrependimento, troca e garantia calculados para a data de hoje |
| `consultar_politicas` | Horário, pagamento, trocas, frete, promoções, garantia, dados da loja | Seção do manual por tópico ou busca por texto; avisa quando o manual não cobre o assunto |

**Por que as regras ficam no código e não no prompt:** cálculos (desconto PIX, parcelas, prazos em dias) e formatação (`R$ 1.234,56`, `dd/mm/aaaa`) são feitos em Python, e o modelo só repassa os valores. Isso evita erros de conta e de data do LLM. Erros de argumento ou de execução nunca derrubam a conversa: voltam ao modelo como `tool_result` com `is_error`, para ele corrigir ou explicar ao cliente.

**Privacidade em `consultar_pedido`:** o pedido só é exibido se o cliente informar número do pedido + telefone ou e-mail, ou, sem o número, telefone **e** e-mail. Se a verificação falhar, a resposta é genérica e não revela se o pedido existe. Só o primeiro nome do cliente volta ao modelo, nunca telefone ou e-mail.

## Agente: prompt de sistema e tool calling

**Loop** ([`agent/core.py`](src/emporio_agent/agent/core.py)):

1. Envia o histórico, a nova mensagem e as ferramentas ao modelo.
2. Se a resposta for `tool_use`, executa **todas** as ferramentas pedidas (podem ser várias em paralelo), devolve os `tool_result` e chama o modelo de novo.
3. Repete até vir uma resposta em texto, com limite de 6 rodadas de ferramentas.
4. Falha da API, resposta vazia ou limite atingido viram uma mensagem amigável ao cliente, e a troca que falhou não entra no histórico.

**Prompt de sistema** ([`agent/prompt.py`](src/emporio_agent/agent/prompt.py)):

- **Tom de voz:** "amigo que entende de música", informal mas profissional, mensagens curtas de chat (manual §7.1).
- **Dados só via ferramentas:** nunca informar preço, estoque, disponibilidade, prazos ou políticas de memória. Disponibilidade só é confirmada após consultar o catálogo; se o produto estiver indisponível, o agente oferece alternativas (§7.3).
- **Escopo:** a loja vende só instrumentos. Pedidos de acessórios (cordas, cabos, pedais, palhetas, amplificadores, cases) são redirecionados com gentileza. Se `PARTNER_STORES` estiver preenchida, o agente indica essas lojas; senão, não cita nenhuma (nunca inventa nomes).
- **Regras financeiras:** 5% no PIX **não acumula** com promoção ativa; promoção é sempre mostrada com preço original e percentual (§6.2); o modelo não recalcula nem cria descontos, e promoção que não veio da ferramenta não existe.
- **Privacidade:** pedido só é consultado com os dados de verificação; o agente pede esses dados antes de chamar a ferramenta.
- **Contatos oficiais:** as divergências do manual (WhatsApp, e-mail, parcelamento) já vêm resolvidas no prompt.
- **Limites:** o agente não fecha vendas, não reserva produtos e não registra trocas; ele orienta e indica os canais oficiais. Tentativas de alterar as regras pela conversa são ignoradas.
- **Contexto dinâmico:** data e hora atuais da loja (para avisar quando o cliente escreve fora do horário de atendimento) e as lojas parceiras.

O prompt é dividido em seções curtas e imperativas (persona → regras invioláveis → como usar cada ferramenta → situações especiais → formato), e as regras numeradas citam a seção do manual de origem. As **descrições das ferramentas** também fazem parte da estratégia: dizem quando usar cada uma ("use SEMPRE que...") e o que pedir ao cliente antes ("se o cliente não informou esses dados, PEÇA-OS").

**Prompt caching:** a parte fixa do prompt (e as definições das ferramentas, que vêm antes dela) é marcada com `cache_control`. A data/hora fica num bloco separado, depois do trecho cacheado, para não invalidar o cache a cada mensagem.

## Interface CLI e histórico

```bash
uv run emporio-chat            # conversa normal
uv run emporio-chat --debug    # mostra as ferramentas chamadas, o tamanho do histórico e erros detalhados
```

Comandos durante a conversa: `/novo` (nova conversa), `/ajuda` e `/sair` (ou Ctrl+C / Ctrl+D).

**Histórico em memória** ([`agent/session.py`](src/emporio_agent/agent/session.py)): a `ChatSession` guarda a conversa da sessão no formato da Messages API, **incluindo as chamadas de ferramenta e seus resultados**. Por isso perguntas de acompanhamento funcionam: depois de "Quanto custa o violão Yamaha C40?", a pergunta "E quanto fica à vista no PIX?" chega ao modelo junto com o produto consultado e o preço PIX que a ferramenta devolveu.

- Acima de 60 mensagens, os turnos mais antigos são descartados. O corte é sempre no início de uma pergunta do cliente, para nunca separar uma chamada de ferramenta do seu resultado (a API rejeitaria o histórico).
- O histórico vive enquanto o CLI estiver aberto.

## Testes

```bash
uv run pytest
```

Os testes **não chamam a API**: um cliente falso ([`tests/fakes.py`](tests/fakes.py)) devolve respostas roteirizadas, enquanto as ferramentas rodam de verdade sobre o banco gerado dos CSVs. Cobrem:

- a ingestão dos dados e o processamento do manual;
- o loop de tool calling: ferramentas em paralelo, argumentos inválidos, falha da API, limite de rodadas;
- o histórico da sessão, incluindo o cenário "E no PIX?";
- o CLI e a configuração (data/hora simulada, lojas parceiras).

Eles verificam a **mecânica** do agente. O comportamento do modelo (escolher a ferramenta certa, seguir as regras) é verificado nas conversas de [`examples/`](examples/), geradas contra a API real.

## Suposições

Onde os dados ou o enunciado não eram explícitos, as decisões foram:

- **Data de recebimento do pedido:** os dados não têm a data real de entrega; para pedidos entregues, usa-se `estimated_delivery` como data de recebimento ao calcular os prazos de 7 dias (arrependimento e troca por preferência) e de 90 dias (garantia legal). A ferramenta informa essa suposição ao modelo.
- **Significado dos status de pedido:** `pending` = aguardando pagamento; `confirmed` = pago, em separação; `shipped` = em trânsito; `delivered` = entregue; `cancelled` = cancelado.
- **Promoção vigente** = `is_active = 1` na tabela de promoções (não há datas de início e fim); com mais de uma ativa no mesmo produto, vale a maior.
- **Acessórios:** a lista do manual (cordas, palhetas, cabos, cases, pedais, amplificadores) é tratada como exemplo; itens equivalentes (afinador, capa, baqueta avulsa) também são recusados.
- **Lojas parceiras:** o manual pede para sugeri-las "quando possível", mas não diz quais são. Ficaram configuráveis e vêm vazias por padrão.
- **Feriados:** o manual diz que a loja fecha em feriados, mas não os lista; o agente não tem um calendário de feriados.

## Limitações conhecidas e próximos passos

**Limitações:**

- **Sem persistência entre execuções:** o histórico some ao fechar o CLI, e há uma única conversa por vez (sem múltiplos usuários).
- **O agente não executa ações:** não reserva produtos, não abre solicitações de troca e não registra reclamações (o manual pede registro e retorno em 24h). Ele orienta e encaminha aos canais oficiais.
- **Busca de produtos por palavras-chave:** pedidos subjetivos ("algo com som mais quente", "para tocar MPB") dependem de o modelo traduzi-los em filtros; não há busca semântica no catálogo.
- **Verificação de identidade simples:** número do pedido + e-mail é um fator fraco; num canal real, seria melhor um código enviado ao telefone cadastrado.
- **Frete:** sem cotação por CEP (o manual diz que é calculado no checkout); o agente só informa as modalidades e os prazos estimados.
- **Sem avaliação automatizada do comportamento do LLM:** os testes cobrem a mecânica; a qualidade das respostas foi verificada nos exemplos, não com uma suíte de avaliação.
- **Histórico longo:** além de 60 mensagens, os turnos antigos são descartados, e não resumidos.
- **Dependência de um único provedor:** sem fallback automático para outro modelo ou provedor se a API da Anthropic falhar; o agente responde com uma mensagem amigável e indica os canais da loja.

**Com mais tempo, eu faria:**

1. **Suíte de avaliação (evals):** um conjunto de perguntas com respostas esperadas (ferramenta correta, valores corretos, recusa de acessórios, não vazar dados), rodado a cada mudança de prompt ou modelo, com checagens determinísticas e um LLM como juiz para o tom.
2. **API HTTP + persistência** de sessões (SQLite ou Redis), para integrar com um canal real como o WhatsApp Business.
3. **Handoff para humano e ações reais:** ferramentas para registrar reclamações e solicitações de troca, e transferir a conversa à equipe com um resumo.
4. **Busca híbrida no catálogo:** palavras-chave + embeddings, para pedidos subjetivos.
5. **Observabilidade:** registrar por conversa as ferramentas chamadas, a latência, os tokens e a taxa de acerto do cache, para acompanhar custo e qualidade em produção.
6. **Calendário de feriados e horários especiais**, e resposta automática fora do expediente.

## Uso de assistentes de código

Usei o **Claude Code** (extensão do VS Code) como par de programação ao longo do projeto. O fluxo de trabalho foi:

1. **Divisão em fases:** quebrei o desafio em etapas (setup → dados → manual → ferramentas → agente → CLI → exemplos e documentação) e passei ao assistente uma especificação curta de cada fase.
2. **Decisões ficaram comigo:** quando havia escolha de produto ou arquitetura, o assistente levantava as opções com prós e contras e eu decidia. Exemplos: SDK nativo × LangGraph, qual modelo usar, o que fazer com lojas parceiras não especificadas no manual.
3. **Implementação com testes:** o assistente lia o código existente, implementava a fase e escrevia os testes; eu revisava o diff, rodava o projeto e fazia o **commit de cada fase manualmente**. Por isso o histórico do Git acompanha as etapas.
4. **Revisão cruzada:** pedi verificações de consistência entre código e documentação. Por exemplo, uma revisão encontrou o número de WhatsApp errado na mensagem de erro, divergente da decisão documentada sobre as inconsistências do manual.
5. **Exemplos não escritos à mão:** as conversas em `examples/` são geradas por script contra a API real, para que reflitam o comportamento verdadeiro do agente.

## Estrutura do projeto

```text
.
├── data/                     # Dados fornecidos pela loja (CSVs + manual em PDF)
├── src/emporio_agent/
│   ├── config.py             # Configuração via variáveis de ambiente
│   ├── text.py               # Normalização de texto (acentos, dígitos)
│   ├── data/                 # Ingestão dos CSVs → SQLite (schema + loader)
│   ├── policies/             # Processamento do manual em PDF → seções
│   ├── tools/                # Ferramentas expostas ao modelo (catálogo, pedidos, políticas)
│   ├── agent/                # Prompt de sistema, loop de tool calling, sessão/histórico
│   └── interface/            # CLI
├── tests/                    # Testes (pytest), com cliente falso da API
├── examples/                 # Conversas de exemplo + script que as gera
├── storage/                  # Banco SQLite gerado (não versionado)
├── .env.example
└── pyproject.toml
```
