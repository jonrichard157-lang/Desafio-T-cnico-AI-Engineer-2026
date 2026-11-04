"""Gera as conversas de exemplo rodando o agente REAL (modelo + ferramentas) e salva em Markdown.

Uso:
    uv run python examples/generate_examples.py          # todos os cenários
    uv run python examples/generate_examples.py 03 04    # só os cenários com esses prefixos

Para reprodutibilidade, a data/hora da loja é fixada em sexta-feira, 20/02/2026, 10:30
(STORE_TODAY) e nenhuma loja parceira é configurada. Nessa data o pedido 7 (cenário 4)
foi recebido há 3 dias, dentro do prazo de arrependimento.

Nada aqui é editado à mão: cada arquivo é a transcrição da execução, incluindo as
ferramentas que o agente decidiu chamar. Se a API falhar em qualquer turno, o cenário
não é salvo (para nunca registrar uma conversa incompleta como exemplo).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Precisa vir antes de qualquer leitura das configurações (o .env não sobrescreve estas).
SIMULATED_NOW = "2026-02-20T10:30"
os.environ["STORE_TODAY"] = SIMULATED_NOW
os.environ["PARTNER_STORES"] = ""

from emporio_agent.agent import ChatSession, build_agent  # noqa: E402
from emporio_agent.agent.core import AgentReply  # noqa: E402
from emporio_agent.config import get_settings  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parent
MAX_TOOL_OUTPUT = 1500


@dataclass(frozen=True)
class Scenario:
    slug: str
    title: str
    demonstrates: str
    messages: list[str]


SCENARIOS = [
    Scenario(
        "01_violoes_ate_1000",
        "Violões disponíveis até R$ 1.000",
        "Busca no catálogo com teto de preço (só itens ativos e com estoque) e recomendação para iniciante "
        "numa pergunta de acompanhamento.",
        [
            "Oi! Quais opções de violões vocês têm disponíveis custando até R$1000?",
            "Qual desses você indicaria pra quem está começando?",
        ],
    ),
    Scenario(
        "02_endereco_horario",
        "Endereço e horário da loja",
        "Informações institucionais vindas do manual (consultar_politicas), usando a data atual "
        "(sexta-feira) para responder sobre 'amanhã' (sábado).",
        [
            "Qual o endereço da loja?",
            "E vocês abrem amanhã? Até que horas?",
        ],
    ),
    Scenario(
        "03_preco_takamine_gd20",
        "Preço do Takamine GD20, promoção expirada e PIX",
        "Consulta de preço de um modelo específico; o cliente cita uma 'Oferta Especial' que existe nos "
        "dados, mas está inativa (o agente não pode prometê-la); preço no PIX e parcelamento vindos da ferramenta.",
        [
            "Quanto custa o Takamine GD20?",
            "Vi que ele estava numa oferta especial, ainda tá valendo?",
            "E se eu pagar à vista no PIX, quanto fica? Dá pra parcelar também?",
        ],
    ),
    Scenario(
        "04_arrependimento_devolucao",
        "Arrependimento de compra e devolução",
        "Caso não trivial: o agente pede dados para verificar a identidade (LGPD), consulta o pedido, aplica "
        "o prazo de 7 dias após o recebimento calculado para a data de hoje e explica frete e reembolso "
        "segundo o manual (§4.1).",
        [
            "Oi, me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7, meu e-mail é leticia.rocha@jmail.com",
            "E quem paga o frete da devolução? Paguei no cartão em 3x, como recebo o dinheiro de volta?",
        ],
    ),
    Scenario(
        "05_acessorios_fora_do_escopo",
        "Pedido de acessórios (fora do escopo)",
        "Recusa educada de acessórios (cordas, cabo, pedal), que a loja não vende, sem inventar lojas "
        "parceiras, e retomada do atendimento com uma busca de instrumento.",
        [
            "Vocês vendem cordas de nylon e um cabo P10? Também queria um pedal de distorção.",
            "Ah, entendi. E guitarra pra iniciante, até uns R$ 1.500, vocês têm?",
        ],
    ),
]


def _tool_block(reply: AgentReply) -> list[str]:
    if not reply.tool_calls:
        return []
    lines = ["<details>", f"<summary>🔧 Ferramentas consultadas ({len(reply.tool_calls)})</summary>", ""]
    for call in reply.tool_calls:
        args = json.dumps(call.input, ensure_ascii=False)
        status = " — **erro**" if call.is_error else ""
        try:
            output = json.dumps(json.loads(call.output), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            output = call.output
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... (resultado truncado)"
        lines += [f"`{call.name}` `{args}`{status}", "", "```json", output, "```", ""]
    lines += ["</details>", ""]
    return lines


def run_scenario(scenario: Scenario) -> str | None:
    session = ChatSession(build_agent())
    settings = get_settings()
    body: list[str] = []
    for message in scenario.messages:
        print(f"  cliente: {message}")
        reply = session.send(message)
        if reply.error:
            print(f"  ERRO: {reply.error}", file=sys.stderr)
            return None
        body += [f"**👤 Cliente:** {message}", ""]
        body += _tool_block(reply)
        body += ["**🎸 Empório:**", "", reply.text, "", "---", ""]

    header = [
        f"# {scenario.title}",
        "",
        f"**O que este exemplo demonstra:** {scenario.demonstrates}",
        "",
        f"- Modelo: `{settings.anthropic_model}`",
        "- Data/hora simulada da loja: sexta-feira, 20/02/2026, 10:30 (`STORE_TODAY`)",
        f"- Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')} por "
        "`uv run python examples/generate_examples.py` (transcrição sem edição manual)",
        "",
        "---",
        "",
    ]
    return "\n".join(header + body).rstrip() + "\n"


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY não configurada (.env).", file=sys.stderr)
        return 1

    selected = [s for s in SCENARIOS if not argv or any(s.slug.startswith(p) for p in argv)]
    failures = 0
    for scenario in selected:
        print(f"[{scenario.slug}] {scenario.title}")
        content = run_scenario(scenario)
        if content is None:
            failures += 1
            print("  -> não salvo (falha na API)\n", file=sys.stderr)
            continue
        path = EXAMPLES_DIR / f"{scenario.slug}.md"
        path.write_text(content, encoding="utf-8")
        print(f"  -> {path.relative_to(EXAMPLES_DIR.parent)}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
