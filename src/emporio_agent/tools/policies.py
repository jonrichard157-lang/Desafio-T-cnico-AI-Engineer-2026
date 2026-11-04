"""Tool that queries the store's Policies and Procedures Manual.

The manual is in Portuguese, so the returned section text is Portuguese and free-text
questions must be written in Portuguese to match its vocabulary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from emporio_agent.policies.manual import TOPICS
from emporio_agent.tools.base import Tool, ToolContext

TopicName = Literal[
    "about_store", "opening_hours", "payment", "returns_exchanges", "shipping",
    "promotions", "customer_service", "warranty", "privacy",
]
assert set(TopicName.__args__) == set(TOPICS), "TopicName must mirror the manual's topics"


class PolicyLookupInput(BaseModel):
    topic: TopicName | None = Field(
        default=None,
        description="Manual topic: " + "; ".join(f"{k} = {v[1]}" for k, v in TOPICS.items()) + ".",
    )
    question: str | None = Field(
        default=None,
        description="Free-text question IN PORTUGUESE, using the manual's vocabulary (e.g. 'reembolso cartão "
        "parcelado'), for when no topic clearly fits.",
    )

    @model_validator(mode="after")
    def _check(self) -> "PolicyLookupInput":
        if not self.topic and not (self.question and self.question.strip()):
            raise ValueError("provide topic or question")
        return self


def lookup_policy(ctx: ToolContext, params: PolicyLookupInput) -> dict:
    manual = ctx.manual
    result: dict = {"source": f"Policies and Procedures Manual v{manual.version} ({manual.updated_at})"}

    if params.topic:
        section = manual.section_for_topic(params.topic)
        result["sections"] = [{"section": section.section_id, "title": section.title,
                               "content": manual.render_section(section.section_id)}]
        return result

    matches = manual.search(params.question or "", k=3)
    if not matches:
        result["sections"] = []
        result["notice"] = (
            "No section of the manual covers this subject. Do not make up policies: say you will check with the "
            "team, or share the store's contact channels."
        )
        return result
    result["sections"] = [{"section": s.section_id, "title": s.title, "content": s.render()} for s, _ in matches]
    return result


LOOKUP_POLICY = Tool(
    name="lookup_policy",
    description=(
        "Queries the store's official policy manual (in Portuguese): address and contacts, opening hours, payment "
        "methods and installments, exchanges and returns, shipping and delivery, promotion rules, warranty and "
        "privacy. ALWAYS use it before answering about rules, deadlines or store information — never answer from "
        "memory. Prefer 'topic'; use 'question' when no topic fits."
    ),
    input_model=PolicyLookupInput,
    handler=lookup_policy,
)
