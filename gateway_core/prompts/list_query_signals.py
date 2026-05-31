"""名单类问法信号（统一口径）。

`PEOPLE_LIST_QUERY_TOKENS` 为通用「要人名/名单」词表，供学校 schema规划、提示词等复用；
`has_leave_people_list_signal` 仅在请假域内做「请假语义 + 名单语义」的合取。
"""

from __future__ import annotations

import re
from typing import Iterable, Tuple

PEOPLE_LIST_QUERY_TOKENS: Tuple[str, ...] = (
    "哪些人",
    "有哪些人",
    "哪些学生",
    "有哪些学生",
    "都有谁",
    "都是谁",
    "名单",
    "姓名",
    "人名",
    "哪几位",
    "哪几名",
    "列出姓名",
    "列出名单",
    "具体是",
    "是哪些人",
    "具体是哪些人",
    "具体有哪些人",
    "具体是谁",
)

PEOPLE_LIST_FOLLOWUP_PHRASES: Tuple[str, ...] = (
    "是哪些人",
    "是哪些同学",
    "是哪些老师",
    "是哪些学生",
    "都有哪些人",
    "都有谁",
    "具体是哪些人",
    "具体有哪些人",
    "具体是谁",
    "名单",
    "姓名",
)

PEOPLE_LIST_SHORT_TOKENS: Tuple[str, ...] = ("哪些人", "都有谁", "名单", "姓名")

LEAVE_QUERY_TOKENS: Tuple[str, ...] = (
    "请假",
    "病假",
    "事假",
    "离校",
    "返校",
    "未返校",
    "销假",
    "未销假",
    "缺勤",
)

LEAVE_PEOPLE_EXTRA_TOKENS: Tuple[str, ...] = ("谁请假", "谁请假了")


def _compact(text: str) -> str:
    """中文注释：封装_compact的内部处理逻辑。"""
    return re.sub(r"\s+", "", str(text or ""))


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    """中文注释：封装_contains_any的内部处理逻辑。"""
    q = _compact(text)
    if not q:
        return False
    return any(token in q for token in tokens)


def has_people_list_signal(text: str) -> bool:
    """中文注释：实现has_people_list_signal的核心业务处理流程。"""
    return _contains_any(text, PEOPLE_LIST_QUERY_TOKENS)


def is_people_list_followup(text: str) -> bool:
    """中文注释：实现is_people_list_followup的核心业务处理流程。"""
    q = _compact(text)
    if not q:
        return False
    if q in PEOPLE_LIST_FOLLOWUP_PHRASES:
        return True
    return len(q) <= 14 and any(token in q for token in PEOPLE_LIST_SHORT_TOKENS)


def has_leave_people_list_signal(text: str) -> bool:
    """中文注释：实现has_leave_people_list_signal的核心业务处理流程。"""
    q = _compact(text)
    if not q:
        return False
    has_leave = _contains_any(q, LEAVE_QUERY_TOKENS)
    has_list = has_people_list_signal(q) or _contains_any(q, LEAVE_PEOPLE_EXTRA_TOKENS)
    return has_leave and has_list


__all__ = [
    "LEAVE_QUERY_TOKENS",
    "PEOPLE_LIST_QUERY_TOKENS",
    "has_leave_people_list_signal",
    "has_people_list_signal",
    "is_people_list_followup",
]
