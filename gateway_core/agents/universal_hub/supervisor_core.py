from __future__ import annotations

from gateway_core.agents.universal_hub.registry import mandatory_candidate_skill_names
from gateway_core.prompts import prompt_domains


def determine_required_outputs(user_query: str, current_outputs: list[str]) -> list[str]:
    """Return per-turn output requirements with deterministic visual intent.

    Visual requests need both audited data and a generated image. The order is
    important: data evidence must be produced before the image skill can bind
    itself to a SQL lineage hash.
    """

    return prompt_domains.resolve_required_outputs(user_query, current_outputs)


def get_mandatory_candidate_skills(required: frozenset[str], completed: frozenset[str]) -> list[str]:
    return mandatory_candidate_skill_names(required, completed)
