from __future__ import annotations

from gateway_core.agents.universal_hub.registry import SKILL_REGISTRY
from gateway_core.prompts import prompt_domains


def determine_required_outputs(user_query: str, current_outputs: list[str]) -> list[str]:
    """Return per-turn output requirements with deterministic visual intent.

    Visual requests need both audited data and a generated image. The order is
    important: data evidence must be produced before the image skill can bind
    itself to a SQL lineage hash.
    """

    query = str(user_query or "").strip().lower()
    base_outputs = tuple(
        output for output in dict.fromkeys(current_outputs) if output not in prompt_domains.TEMPORARY_OUTPUT_SLOTS
    )
    triggered_outputs = tuple(
        output
        for rule in prompt_domains.REQUIRED_OUTPUT_RULES
        for output in rule.outputs
        if any(keyword in query for keyword in rule.keywords)
    )
    required_set = set(base_outputs) | set(triggered_outputs)
    ordered_outputs = tuple(dict.fromkeys((*base_outputs, *triggered_outputs)))
    return [output for output in ordered_outputs if output in required_set]


def get_mandatory_candidate_skills(required: frozenset[str], completed: frozenset[str]) -> list[str]:
    missing_outputs = frozenset(required).difference(completed)
    if not missing_outputs:
        return ["FINISH"]
    candidates: list[str] = []
    for skill_name, spec in SKILL_REGISTRY.items():
        if spec.outputs.intersection(missing_outputs):
            candidates.append(skill_name)
    return candidates
