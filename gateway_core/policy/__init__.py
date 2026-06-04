"""Official policy detection and evidence retrieval."""

from gateway_core.policy.official_policy import (
    OfficialPolicySearchConfig,
    build_official_policy_search,
    resolve_policy_time_scope,
    search_official_policy_chunks,
    search_official_policy_evidence,
)

__all__ = [
    "OfficialPolicySearchConfig",
    "build_official_policy_search",
    "resolve_policy_time_scope",
    "search_official_policy_chunks",
    "search_official_policy_evidence",
]
