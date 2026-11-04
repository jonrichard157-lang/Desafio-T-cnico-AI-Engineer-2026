"""Interface de terminal: conversa com o agente, com histórico em memória na sessão.

Uso:
    uv run emporio-chat            # conversa normal
    uv run emporio-chat --debug    # mostra as ferramentas chamadas a cada resposta

Comandos durante a conversa: /novo (nova conversa), /ajuda, /sair.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Callable

from emporio_agent.agent.session import ChatSession
from emporio_agent.config import get_settings

BANNER = """\
🎸 Empório da Música — atendimento virtual
Digite sua mensagem e tecle Enter. Comandos: /novo (nova conversa), /ajuda, /sair
"""
HELP = """\
/novo   começa uma nova conversa (apaga o histórico desta sessão)
/ajuda  mostra esta ajuda
/sair   encerra (também: Ctrl+C ou Ctrl+D)
"""
EXIT_COMMANDS = {"/sair", "/exit", "/quit", "sair"}


def _preview(text: str, size: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


def run(
    session: ChatSession,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    debug: bool = False,
) -> None:
    """Loop de conversa. `read`/`write` são injetáveis para testes."""
    write(BANNER)
    while True:
        try:
            message = read("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("\nAté mais! 🎶")
            return

        if not message:
            continue
        command = message.lower()
        if command in EXIT_COMMANDS:
            write("Até mais! 🎶")
            return
        if command == "/ajuda":
            write(HELP)
            continue
        if command == "/novo":
            session.reset()
            write("(nova conversa iniciada)\n")
            continue

        try:
            reply = session.send(message)
        except KeyboardInterrupt:
            write("\n(resposta cancelada)\n")
            continue

        if debug:
            for call in reply.tool_calls:
                status = "ERRO" if call.is_error else "ok"
                write(f"  [tool] {call.name}({json.dumps(call.input, ensure_ascii=False)}) -> {status}: "
                      f"{_preview(call.output)}")
            write(f"  [sessão] {session.turns} turno(s), {len(session.history)} mensagens no histórico")
            if reply.error:
                write(f"  [erro] {reply.error}")
        write(f"\nEmpório: {reply.text}\n")


def _configure_console() -> None:
    """Garante UTF-8 no terminal do Windows (acentos e emojis)."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(prog="emporio-chat", description="Atendimento da Empório da Música no terminal.")
    parser.add_argument("--debug", action="store_true", help="mostra as ferramentas chamadas e logs detalhados")
    args = parser.parse_args(argv)

    # Sem --debug o cliente vê só a mensagem amigável; com --debug, o erro e os logs completos.
    logging.basicConfig(level=logging.INFO if args.debug else logging.CRITICAL,
                        format="%(levelname)s %(name)s: %(message)s")

    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY não configurada. Copie .env.example para .env e preencha a chave.", file=sys.stderr)
        return 1

    from emporio_agent.agent import build_agent  # importa aqui para o --help ser rápido

    run(ChatSession(build_agent()), debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
