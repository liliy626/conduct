from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LineageRouteDecision:
    domain_key: str
    confidence: float
    reason: str
    matched_tables: list[str]
    matched_keyword: str = ""


TARGET_TABLE_LINEAGE_OVERRIDES = {
    "人事线": (
        "leaves",
        "attendance",
        "teacher_leave",
        "教师销假",
        "请假明细",
        "销假",
    ),
}

DOMAIN_ROUTER_MATRIX = {
    "党政核心层": (
        "党支部",
        "校长室",
        "校办",
        "党政",
        "公章",
        "会务",
        "发展规划",
        "校园安全总责",
        "行政大盘",
    ),
    "教学线": (
        "教务处",
        "教务",
        "课程教学",
        "研究生院",
        "课表",
        "排课",
        "代课",
        "备勤",
        "教材",
        "学籍",
        "教学常规",
        "教研组运行",
        "教学",
    ),
    "学生与德育线": (
        "德育处",
        "学工部",
        "研工部",
        "少先队",
        "班主任",
        "学生行为规范",
        "行规红黑榜",
        "眼保健操",
        "行规",
        "德育",
        "扣分",
        "违纪",
        "纪律",
        "家校",
        "法治",
    ),
    "人事线": (
        "人事处",
        "教师工作部",
        "教师请假",
        "教研组请假",
        "师资结构",
        "请假频率",
        "教师工作量",
        "教师培训学分",
        "职称评审",
        "跨年级代课",
        "假勤",
        "考勤",
        "销假",
        "teacher_leave",
        "leave",
    ),
    "后勤保障线": (
        "总务处",
        "后勤保障",
        "国资处",
        "资产采购",
        "食堂",
        "餐饮",
        "基建",
        "维修",
        "门禁",
        "消防",
        "报修",
        "物业",
        "预算",
    ),
    "科研线": (
        "教科室",
        "教师发展部",
        "科研处",
        "课题申报",
        "论文发表",
        "科研经费",
        "学术成果",
        "职称评定",
        "科研",
        "课题",
        "论文",
        "成果",
    ),
    "群团与监督": (
        "工会",
        "纪委",
        "监察处",
        "学术委员会",
        "教代会",
        "职工福利",
        "退管",
        "女工",
        "纪检",
        "统战",
        "合规",
        "监督",
    ),
}


def decide_lineage_route(
    *,
    question: str,
    table_names: list[str],
    columns: list[str],
    field_labels: dict[str, str] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> LineageRouteDecision:
    lineage_decision = _lineage_override(table_names)
    if lineage_decision:
        return lineage_decision

    haystack = " ".join(
        [
            str(question or ""),
            " ".join(str(item or "") for item in table_names),
            " ".join(str(item or "") for item in columns),
            " ".join(str(key or "") for key in (field_labels or {})),
            " ".join(str(value or "") for value in (field_labels or {}).values()),
            " ".join(str(key or "") for row in (rows or [])[:1] for key in row),
        ]
    ).lower()
    match_len, domain, keyword = max(
        [(0, "通用智慧校园", "")]
        + [
            (len(str(token)), domain, str(token))
            for domain, tokens in DOMAIN_ROUTER_MATRIX.items()
            for token in tokens
            if str(token).lower() in haystack
        ],
        key=lambda item: item[0],
    )
    return LineageRouteDecision(
        domain_key=domain,
        confidence=0.8 if match_len else 0.2,
        reason="keyword_match" if match_len else "fallback",
        matched_tables=[],
        matched_keyword=keyword,
    )


def _lineage_override(table_names: list[str]) -> LineageRouteDecision | None:
    normalized_tables = [str(item or "") for item in table_names]
    for domain, tokens in TARGET_TABLE_LINEAGE_OVERRIDES.items():
        matched = [table for table in normalized_tables if any(str(token).lower() in table.lower() for token in tokens)]
        if matched:
            return LineageRouteDecision(
                domain_key=domain,
                confidence=1.0,
                reason="table_lineage_override",
                matched_tables=matched,
            )
    return None
