"""Ferramenta de consulta ao Manual de Políticas e Procedimentos da loja."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from emporio_agent.policies.manual import TOPICS
from emporio_agent.tools.base import Tool, ToolContext

TopicName = Literal[
    "sobre_a_loja", "horario", "pagamento", "trocas_devolucoes", "frete_entrega",
    "promocoes", "atendimento", "garantia", "privacidade",
]
assert set(TopicName.__args__) == set(TOPICS), "TopicName precisa refletir os tópicos do manual"


class PolicyLookupInput(BaseModel):
    topico: TopicName | None = Field(
        default=None,
        description="Tópico do manual: " + "; ".join(f"{k} = {v[1]}" for k, v in TOPICS.items()) + ".",
    )
    pergunta: str | None = Field(
        default=None,
        description="Pergunta em texto livre, para quando nenhum tópico se encaixar claramente.",
    )

    @model_validator(mode="after")
    def _check(self) -> "PolicyLookupInput":
        if not self.topico and not (self.pergunta and self.pergunta.strip()):
            raise ValueError("informe topico ou pergunta")
        return self


def lookup_policy(ctx: ToolContext, params: PolicyLookupInput) -> dict:
    manual = ctx.manual
    result: dict = {"fonte": f"Manual de Políticas e Procedimentos v{manual.version} ({manual.updated_at})"}

    if params.topico:
        section = manual.section_for_topic(params.topico)
        result["secoes"] = [{"secao": section.section_id, "titulo": section.title,
                             "conteudo": manual.render_section(section.section_id)}]
        return result

    matches = manual.search(params.pergunta or "", k=3)
    if not matches:
        result["secoes"] = []
        result["aviso"] = (
            "Nenhuma seção do manual trata desse assunto. Não invente políticas: informe que vai verificar com a "
            "equipe ou ofereça os canais de contato da loja."
        )
        return result
    result["secoes"] = [{"secao": s.section_id, "titulo": s.title, "conteudo": s.render()} for s, _ in matches]
    return result


LOOKUP_POLICY = Tool(
    name="consultar_politicas",
    description=(
        "Consulta o manual oficial de políticas da loja: endereço e contatos, horário de funcionamento, formas de "
        "pagamento e parcelamento, trocas e devoluções, frete e entrega, regras de promoções, garantia e "
        "privacidade. Use SEMPRE antes de responder sobre regras, prazos ou informações institucionais — "
        "nunca responda de memória. Prefira 'topico'; use 'pergunta' quando nenhum tópico se encaixar."
    ),
    input_model=PolicyLookupInput,
    handler=lookup_policy,
)

