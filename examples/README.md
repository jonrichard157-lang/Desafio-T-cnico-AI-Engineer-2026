# Exemplos de interação

Cada arquivo `.md` desta pasta é a transcrição de uma conversa **real** com o agente: modelo da Anthropic + ferramentas sobre os dados da loja. Ele é gerado pelo script [`generate_examples.py`](generate_examples.py), sem edição manual. Cada turno mostra as ferramentas que o agente decidiu chamar, com argumentos e resultado (bloco recolhível "🔧 Ferramentas consultadas").

| Arquivo | Cenário | O que demonstra |
|---|---|---|
| [`01_violoes_ate_1000.md`](01_violoes_ate_1000.md) | "Quais opções de violões disponíveis custando até R\$ 1000?" | Busca no catálogo com teto de preço; só itens ativos e com estoque; recomendação na pergunta seguinte |
| [`02_endereco_horario.md`](02_endereco_horario.md) | "Qual o endereço da loja?" + "Abrem amanhã?" | Consulta ao manual; uso da data atual (sexta) para responder sobre sábado |
| [`03_preco_takamine_gd20.md`](03_preco_takamine_gd20.md) | "Quanto custa o Takamine GD20?" | Preço de um modelo; promoção citada pelo cliente que está **inativa** nos dados; PIX e parcelamento |
| [`04_arrependimento_devolucao.md`](04_arrependimento_devolucao.md) | "Me arrependi da minha compra, posso devolver?" | **Caso não trivial:** verificação de identidade, consulta do pedido, prazo de 7 dias calculado para a data de hoje, frete e reembolso pelo manual |
| [`05_acessorios_fora_do_escopo.md`](05_acessorios_fora_do_escopo.md) | Cordas, cabo P10 e pedal | Recusa de acessórios (fora do escopo) sem inventar lojas parceiras; retomada com busca de instrumento |

## Como gerar novamente

```bash
uv run python examples/generate_examples.py          # todos os cenários
uv run python examples/generate_examples.py 03 04    # só alguns, pelo prefixo
```

- A data e a hora da loja são fixadas em **sexta-feira, 20/02/2026, 10:30**, para os prazos e o horário de funcionamento serem reproduzíveis. Nessa data, o pedido 7 do cenário 4 foi recebido há 3 dias.
- Nenhuma loja parceira é configurada (`PARTNER_STORES` vazio).
- Se a API falhar em qualquer turno, o cenário **não é salvo**, para que uma conversa incompleta nunca vire exemplo.
- Respostas de LLM variam entre execuções: o texto muda, mas as regras (valores, prazos, escopo) vêm das ferramentas e devem se manter.
