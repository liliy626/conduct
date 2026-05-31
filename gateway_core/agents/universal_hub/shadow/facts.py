from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


DOMAIN_KEYWORDS = (
    "平均",
    "均分",
    "请假",
    "病假",
    "事假",
    "挂科率",
    "积分",
    "获奖",
    "扣分",
    "排名",
    "占比",
)


@dataclass(frozen=True)
class FactSignature:
    numbers: tuple[str, ...]
    keywords: frozenset[str]


def extract_fact_signature(text: str) -> FactSignature:
    return FactSignature(numbers=_normalized_numbers(text), keywords=_keywords(text))


def fact_signature_matches(left: str, right: str) -> bool:
    left_sig = extract_fact_signature(left)
    right_sig = extract_fact_signature(right)
    if left_sig.numbers and right_sig.numbers and left_sig.numbers != right_sig.numbers:
        return False
    if left_sig.keywords and right_sig.keywords:
        return bool(left_sig.keywords.intersection(right_sig.keywords))
    return True


def _normalized_numbers(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw in re.findall(r"\d+(?:\.\d+)?", str(text or "")):
        try:
            value = Decimal(raw).normalize()
        except InvalidOperation:
            continue
        values.append(format(value, "f"))
    return tuple(values)


def _keywords(text: str) -> frozenset[str]:
    value = str(text or "")
    return frozenset(keyword for keyword in DOMAIN_KEYWORDS if keyword in value)
