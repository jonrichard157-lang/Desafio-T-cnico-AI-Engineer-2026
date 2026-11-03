# Empório da Música — Agente de Atendimento

Protótipo de agente de atendimento por mensagens de texto para a **Empório da Música**, loja (fictícia) de instrumentos musicais em Campo Grande/MS. Desenvolvido como parte do Desafio Técnico de AI Engineer.

O agente usa **Claude (Anthropic)** com *tool calling* para decidir, a cada mensagem, se precisa consultar **dados operacionais** (catálogo, estoque, promoções, pedidos) ou **políticas da loja** (horários, trocas, pagamentos, frete), e responde no tom definido pelo manual de atendimento.

> 🚧 Documento em construção — será completado ao longo das etapas de implementação.

## Status das etapas

- [x] 1. Setup do projeto (ambiente, dependências, configuração)
- [x] 2. Ingestão e tratamento dos dados (CSV → SQLite)
- [ ] 3. Processamento do manual de políticas (PDF → seções)
- [ ] 4. Ferramentas (tools) do agente + testes
- [ ] 5. Agente (prompt de sistema + loop de tool calling)
- [ ] 6. Interfaces (CLI + API HTTP) e persistência de histórico
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

# 3. (opcional) Construir o banco manualmente e ver o relatório de qualidade dos dados.
#    O agente também constrói/atualiza o banco automaticamente ao iniciar.
uv run python -m emporio_agent.data.loader

# 4. Rodar os testes
uv run pytest
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

## Estrutura do projeto

```text
.
├── data/                     # Dados fornecidos pela loja (CSVs + manual em PDF)
├── src/emporio_agent/
│   ├── config.py             # Configuração via variáveis de ambiente
│   ├── data/                 # Ingestão e acesso aos dados operacionais
│   ├── policies/             # Processamento do manual de políticas
│   ├── tools/                # Ferramentas expostas ao modelo
│   ├── agent/                # Prompt, loop de tool calling, memória
│   └── interface/            # CLI e API HTTP
├── tests/
├── examples/                 # Conversas de exemplo com o agente
├── .env.example
└── pyproject.toml
```

