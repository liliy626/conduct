from __future__ import annotations

from gateway_core.agents.universal_hub.registry import SKILL_REGISTRY


def get_mandatory_candidate_skills(required: frozenset[str], completed: frozenset[str]) -> list[str]:
    missing_outputs = frozenset(required).difference(completed)
    if not missing_outputs:
        return ["FINISH"]
    candidates: list[str] = []
    for skill_name, spec in SKILL_REGISTRY.items():
        if spec.outputs.intersection(missing_outputs):
            candidates.append(skill_name)
    return candidates
