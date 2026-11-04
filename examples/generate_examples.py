"""Generates the example conversations by running the REAL agent (model + tools) and saving them as Markdown.

Usage:
    uv run python examples/generate_examples.py          # all scenarios
    uv run python examples/generate_examples.py 03 04    # only scenarios with these prefixes

For reproducibility, the store's date/time is pinned to Friday, 20/02/2026, 10:30
(STORE_TODAY) and no partner store is configured. On that date order 7 (scenario 4)
was received 3 days earlier, within the 7-day right-of-withdrawal period.

Nothing here is edited by hand: each file is the transcript of a run, including the
tools the agent decided to call. If the API fails on any turn, the scenario is not
saved (so an incomplete conversation is never recorded as an example).

The customers write in English here, so reviewers can read the conversations; the agent
replies in the customer's language (its default for the store's real audience is
Brazilian Portuguese). The agent still searches the catalog and the manual in Portuguese,
which is visible in the tool calls.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Must run before the settings are read for the first time (.env does not override these).
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
        "01_acoustic_guitars_up_to_1000",
        "Acoustic guitars up to R$ 1,000",
        "Catalog search with a price cap (only active, in-stock items) and a beginner recommendation in a "
        "follow-up question.",
        [
            "Hi! What acoustic guitars do you have available for up to R$1000?",
            "Which of those would you recommend for a beginner?",
        ],
    ),
    Scenario(
        "02_address_and_opening_hours",
        "Store address and opening hours",
        "Store information taken from the policy manual (lookup_policy), using the current date (Friday) to "
        "answer about 'tomorrow' (Saturday).",
        [
            "What's the store's address?",
            "Are you open tomorrow? Until what time?",
        ],
    ),
    Scenario(
        "03_takamine_gd20_price",
        "Takamine GD20 price, expired promotion and PIX",
        "Price check of a specific model; the customer mentions a 'special offer' that exists in the data but is "
        "inactive (the agent must not promise it); PIX price and installments come from the tool.",
        [
            "How much is the Takamine GD20?",
            "I saw it was on a special offer, is that still valid?",
            "And if I pay upfront with PIX, how much is it? Can I also pay in installments?",
        ],
    ),
    Scenario(
        "04_purchase_regret_and_return",
        "Purchase regret and return",
        "Non-trivial case: the agent asks for data to verify the customer's identity (LGPD), looks up the order, "
        "applies the 7-days-after-receipt deadline computed for today's date, and explains return shipping and "
        "refund according to the manual (section 4.1).",
        [
            "Hi, I regret my purchase. Can I return my order?",
            "It's order 7, my e-mail is leticia.rocha@jmail.com",
            "And who pays for the return shipping? I paid by card in 3 installments, how do I get my money back?",
        ],
    ),
    Scenario(
        "05_accessories_out_of_scope",
        "Accessory request (out of scope)",
        "Polite refusal of accessories (strings, cable, pedal), which the store does not sell, without making up "
        "partner stores, then back to helping with an instrument search.",
        [
            "Do you sell nylon strings and a P10 cable? I'd also like a distortion pedal.",
            "Oh, I see. Do you have a beginner electric guitar for up to about R$ 1,500?",
        ],
    ),
]


def _tool_block(reply: AgentReply) -> list[str]:
    if not reply.tool_calls:
        return []
    lines = ["<details>", f"<summary>🔧 Tools called ({len(reply.tool_calls)})</summary>", ""]
    for call in reply.tool_calls:
        args = json.dumps(call.input, ensure_ascii=False)
        status = " — **error**" if call.is_error else ""
        try:
            output = json.dumps(json.loads(call.output), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            output = call.output
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... (result truncated)"
        lines += [f"`{call.name}` `{args}`{status}", "", "```json", output, "```", ""]
    lines += ["</details>", ""]
    return lines


def run_scenario(scenario: Scenario) -> str | None:
    session = ChatSession(build_agent())
    settings = get_settings()
    body: list[str] = []
    for message in scenario.messages:
        print(f"  customer: {message}")
        reply = session.send(message)
        if reply.error:
            print(f"  ERROR: {reply.error}", file=sys.stderr)
            return None
        body += [f"**👤 Customer:** {message}", ""]
        body += _tool_block(reply)
        body += ["**🎸 Empório (agent):**", "", reply.text, "", "---", ""]

    header = [
        f"# {scenario.title}",
        "",
        f"**What this example shows:** {scenario.demonstrates}",
        "",
        "> The customer writes in English and the agent replies in English (it answers in the customer's "
        "language; Brazilian Portuguese is its default). The catalog and the manual are in Portuguese, so the "
        "agent's search terms and the tool results are partly Portuguese. Tool calls are shown in the "
        "collapsible blocks.",
        "",
        f"- Model: `{settings.anthropic_model}`",
        "- Simulated store date/time: Friday, 20/02/2026, 10:30 (`STORE_TODAY`)",
        f"- Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')} by "
        "`uv run python examples/generate_examples.py` (unedited transcript)",
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
        print("ANTHROPIC_API_KEY is not set (.env).", file=sys.stderr)
        return 1

    selected = [s for s in SCENARIOS if not argv or any(s.slug.startswith(p) for p in argv)]
    failures = 0
    for scenario in selected:
        print(f"[{scenario.slug}] {scenario.title}")
        content = run_scenario(scenario)
        if content is None:
            failures += 1
            print("  -> not saved (API failure)\n", file=sys.stderr)
            continue
        path = EXAMPLES_DIR / f"{scenario.slug}.md"
        path.write_text(content, encoding="utf-8")
        print(f"  -> {path.relative_to(EXAMPLES_DIR.parent)}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
