# Empório da Música — Agente de Atendimento

Protótipo de agente de atendimento por mensagens de texto para a **Empório da Música**, loja (fictícia) de instrumentos musicais em Campo Grande/MS. Desenvolvido como parte do Desafio Técnico de AI Engineer.

O agente usa **Claude (Anthropic)** com *tool calling* para decidir, a cada mensagem, se precisa consultar **dados operacionais** (catálogo, estoque, promoções, pedidos) ou **políticas da loja** (horários, trocas, pagamentos, frete), e responde no tom definido pelo manual de atendimento.

> 🚧 Documento em construção — será completado ao longo das etapas de implementação.

## Status das etapas

- [x] 1. Setup do projeto (ambiente, dependências, configuração)
- [ ] 2. Ingestão e tratamento dos dados (CSV → SQLite)
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
```

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

