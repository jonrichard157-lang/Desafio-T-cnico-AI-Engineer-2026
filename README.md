# Empório da Música — Agente de Atendimento

Protótipo de agente de atendimento por mensagens de texto para a **Empório da Música**, loja (fictícia) de instrumentos musicais em Campo Grande/MS. Desenvolvido como parte do Desafio Técnico de AI Engineer.

O agente usa **Claude (Anthropic)** com *tool calling* para decidir, a cada mensagem, se precisa consultar **dados operacionais** (catálogo, estoque, promoções, pedidos) ou **políticas da loja** (horários, trocas, pagamentos, frete), e responde no tom definido pelo manual de atendimento.

> 🚧 Documento em construção — será completado ao longo das etapas de implementação.

## Status das etapas

- [x] 1. Setup do projeto (ambiente, dependências, configuração)
- [x] 2. Ingestão e tratamento dos dados (CSV → SQLite)
- [x] 3. Processamento do manual de políticas (PDF → seções)
- [x] 4. Ferramentas (tools) do agente
- [x] 5. Agente (prompt de sistema + loop de tool calling)
- [x] 6a. Interface CLI com histórico da conversa em memória
- [ ] 6b. API HTTP e persistência de histórico
- [ ] 7. Exemplos de interação e documentação final

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gerencia o Python e as dependências)
- Python 3.11+ (o `uv` instala automaticamente se necessário)
- Uma chave de API da Anthropic

## Configuração

```bash
# 1. Instalar dependências (cria o ambiente virtual em .venv)
uv sync

# 2. Configurar variáveis de ambiente
cp .env.example .env      # no Windows (PowerShell): Copy-Item .env.example .env
# edite o .env e preencha ANTHROPIC_API_KEY
# (opcional) PARTNER_STORES: lojas parceiras indicadas quando o cliente pede acessórios

# 3. (opcional) Construir o banco manualmente e ver o relatório de qualidade dos dados.
#    O agente também constrói/atualiza o banco automaticamente ao iniciar.
uv run python -m emporio_agent.data.loader

# 4. Rodar os testes (não chamam a API: usam um cliente falso)
uv run pytest

# 5. Conversar com o agente no terminal
uv run emporio-chat
```

## Tratamento dos dados

Os CSVs são carregados em um banco **SQLite** (`storage/emporio.db`), gerado por [`loader.py`](src/emporio_agent/data/loader.py) com o schema em [`schema.sql`](src/emporio_agent/data/schema.sql).

**Por que SQLite e não RAG sobre os CSVs?** As perguntas sobre catálogo e pedidos exigem **filtros exatos** (preço ≤ R\$ 1.000, estoque > 0, status do pedido). Busca vetorial é aproximada e pode "inventar" preço ou estoque. Com SQL a resposta é determinística, e o SQLite já vem com o Python, sem infraestrutura extra.

**Tratamentos aplicados:**

- Tipagem (preço → `REAL`, estoque → `INTEGER`), `CHECK` nos status e chaves estrangeiras validadas na carga.
- Campo `specs` (JSON) validado e incluído na busca.
- `search_text`: nome + categoria + descrição + specs em minúsculas e sem acento, para que "violao" encontre "Violão".
- Telefone só com dígitos e e-mail em minúsculas, para identificar o cliente independente do formato digitado.
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
| Pedidos 3 e 20 | Total ≠ soma dos itens a preço atual | Vale o total registrado (preço pode ter mudado ou havia desconto na época) |
| E-mail `bruno.martins@jmail.com` | Compartilhado por 2 clientes | O e-mail sozinho não basta para identificar o cliente |

## Manual de políticas (retrieval)

O PDF é processado por [`manual.py`](src/emporio_agent/policies/manual.py). Para ver o resultado:

```bash
uv run python -m emporio_agent.policies.manual
```

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
| `detalhes_produto` | Um modelo específico: preço, PIX, parcelamento, estoque, specs | Promoção × PIX não cumulativos; parcelamento do §3.1; alternativas se indisponível (§7.3) |
| `consultar_pedido` | Status, rastreio, troca, devolução, garantia | Verificação de identidade (LGPD); prazos de arrependimento/troca/garantia calculados para a data de hoje |
| `consultar_politicas` | Horário, pagamento, trocas, frete, promoções, garantia, dados da loja | Seção do manual por tópico ou busca por texto; avisa quando o manual não cobre o assunto |

**Por que as regras ficam no código e não no prompt:** cálculos (desconto PIX, parcelas, prazos em dias) e formatação (`R$ 1.234,56`, `dd/mm/aaaa`) são feitos em Python, e o modelo só repassa os valores. Isso evita erros de conta e de data do LLM. Erros de argumento ou de execução nunca derrubam a conversa: voltam ao modelo como `tool_result` com `is_error`, para ele corrigir ou explicar ao cliente.

## Agente: prompt de sistema e tool calling

**Stack:** SDK oficial da Anthropic (`anthropic`), com *function calling* nativo e um loop próprio, sem LangChain/LangGraph. O projeto tem 4 ferramentas e um único agente, então um framework de orquestração traria dependências e camadas de adaptação sem ganho real. O loop direto é curto, fácil de testar e deixa explícito cada passo. O modelo é configurado por `ANTHROPIC_MODEL` (padrão `claude-sonnet-4-5`).

**Loop** ([`agent/core.py`](src/emporio_agent/agent/core.py)):

1. Envia o histórico, a nova mensagem e as ferramentas ao modelo.
2. Se a resposta for `tool_use`, executa **todas** as ferramentas pedidas (podem ser várias em paralelo), devolve os `tool_result` e chama o modelo de novo.
3. Repete até vir uma resposta em texto, com limite de 6 rodadas de ferramentas.
4. Falha da API, resposta vazia ou limite atingido viram uma mensagem amigável ao cliente, e a troca que falhou não entra no histórico.

**Prompt de sistema** ([`agent/prompt.py`](src/emporio_agent/agent/prompt.py)):

- **Tom de voz:** "amigo que entende de música", informal mas profissional, mensagens curtas de chat (manual §7.1).
- **Dados só via ferramentas:** nunca informar preço, estoque, disponibilidade, prazos ou políticas de memória. Disponibilidade só é confirmada após consultar o catálogo; se o produto estiver indisponível, o agente oferece alternativas (§7.3).
- **Escopo:** a loja vende só instrumentos. Pedidos de acessórios (cordas, cabos, pedais, palhetas, amplificadores, cases) são redirecionados com gentileza. Se `PARTNER_STORES` estiver preenchida, o agente indica essas lojas; senão, não cita nenhuma (nunca inventa nomes).
- **Regras financeiras:** 5% no PIX **não acumula** com promoção ativa; promoção é sempre mostrada com preço original e percentual (§6.2); o modelo não recalcula nem cria descontos.
- **Privacidade:** pedido só é consultado com número + telefone/e-mail (ou telefone + e-mail).
- **Limites:** o agente não fecha vendas, não reserva produtos e não registra trocas; ele orienta e indica os canais oficiais. Tentativas de alterar as regras pela conversa são ignoradas.
- **Contexto dinâmico:** data e hora atuais da loja (para avisar quando o cliente escreve fora do horário de atendimento) e as lojas parceiras.

**Prompt caching:** a parte fixa do prompt (e as definições das ferramentas, que vêm antes dela) é marcada com `cache_control`. A data/hora fica num bloco separado, depois do trecho cacheado, para não invalidar o cache a cada mensagem.

## Conversando pelo terminal (CLI)

```bash
uv run emporio-chat            # conversa normal
uv run emporio-chat --debug    # mostra as ferramentas chamadas, o tamanho do histórico e erros detalhados
```

Comandos durante a conversa: `/novo` (nova conversa), `/ajuda` e `/sair` (ou Ctrl+C / Ctrl+D).

**Histórico em memória** ([`agent/session.py`](src/emporio_agent/agent/session.py)): a `ChatSession` guarda a conversa da sessão no formato da Messages API, **incluindo as chamadas de ferramenta e seus resultados**. Por isso perguntas de acompanhamento funcionam: depois de "Quanto custa o violão Yamaha C40?", a pergunta "E quanto fica à vista no PIX?" chega ao modelo junto com o produto consultado e o preço PIX que a ferramenta devolveu.

- Acima de 60 mensagens, os turnos mais antigos são descartados. O corte é sempre no início de uma pergunta do cliente, para nunca separar uma chamada de ferramenta do seu resultado (a API rejeitaria o histórico).
- O histórico vive enquanto o CLI estiver aberto; persistência entre execuções fica para a API HTTP.

## Testes

```bash
uv run pytest
```

Os testes não chamam a API: um cliente falso ([`tests/fakes.py`](tests/fakes.py)) devolve respostas roteirizadas, enquanto as ferramentas rodam de verdade sobre o banco gerado dos CSVs. Cobrem a ingestão dos dados, o processamento do manual, o loop de tool calling (ferramentas em paralelo, argumentos inválidos, falha da API, limite de rodadas), o histórico da sessão (incluindo o cenário "E no PIX?") e o CLI.

## Estrutura do projeto

```text
.
├── data/                     # Dados fornecidos pela loja (CSVs + manual em PDF)
├── src/emporio_agent/
│   ├── config.py             # Configuração via variáveis de ambiente
│   ├── data/                 # Ingestão e acesso aos dados operacionais
│   ├── policies/             # Processamento do manual de políticas
│   ├── tools/                # Ferramentas expostas ao modelo
│   ├── agent/                # Prompt de sistema, loop de tool calling, sessão/histórico
│   └── interface/            # CLI (cli.py) e, na próxima etapa, API HTTP
├── tests/
├── examples/                 # Conversas de exemplo com o agente
├── .env.example
└── pyproject.toml
```

