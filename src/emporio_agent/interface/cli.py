"""Terminal interface: chat with the agent, keeping the history in memory for the session.

Usage:
    uv run emporio-chat            # normal chat
    uv run emporio-chat --debug    # also shows the tools called for each reply

Chat commands: /novo or /new (new conversation), /ajuda or /help, /sair or /exit.

Text the customer reads (banner, prompts, replies) is in Brazilian Portuguese;
debug output and errors meant for developers are in English.
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
/novo  (/new)   começa uma nova conversa (apaga o histórico desta sessão)
/ajuda (/help)  mostra esta ajuda
/sair  (/exit)  encerra (também: Ctrl+C ou Ctrl+D)
"""
EXIT_COMMANDS = {"/sair", "/exit", "/quit", "sair"}
HELP_COMMANDS = {"/ajuda", "/help"}
NEW_COMMANDS = {"/novo", "/new"}


def _preview(text: str, size: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


def run(
    session: ChatSession,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    debug: bool = False,
) -> None:
    """Chat loop. `read`/`write` are injectable for tests."""
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
        if command in HELP_COMMANDS:
            write(HELP)
            continue
        if command in NEW_COMMANDS:
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
                status = "ERROR" if call.is_error else "ok"
                write(f"  [tool] {call.name}({json.dumps(call.input, ensure_ascii=False)}) -> {status}: "
                      f"{_preview(call.output)}")
            write(f"  [session] {session.turns} turn(s), {len(session.history)} messages in history")
            if reply.error:
                write(f"  [error] {reply.error}")
        write(f"\nEmpório: {reply.text}\n")


def _configure_console() -> None:
    """Forces UTF-8 on the Windows terminal (accents and emojis)."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(prog="emporio-chat", description="Empório da Música customer service chat.")
    parser.add_argument("--debug", action="store_true", help="show the tools called and detailed logs")
    args = parser.parse_args(argv)

    # Without --debug the customer only sees the friendly message; with --debug, the error and full logs too.
    logging.basicConfig(level=logging.INFO if args.debug else logging.CRITICAL,
                        format="%(levelname)s %(name)s: %(message)s")

    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill in the key.", file=sys.stderr)
        return 1

    from emporio_agent.agent import build_agent  # imported here so --help stays fast

    run(ChatSession(build_agent()), debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
