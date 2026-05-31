"""时间范围解析工具模块。

职责：
- 负责该领域问句的识别、数据查询与结果上下文组织；
- 为路由层与回答层提供稳定、可复用的特征能力。
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

@dataclass
class TimeRange:
    mode: str
    start: Optional[date]
    end: Optional[date]


@dataclass
class TimeAutoExpandResult:
    rows: List[Any]
    time_meta: Dict[str, Any]
    fallback_to_all_time: bool
    auto_expand_applied: bool
    auto_expand_steps: List[Dict[str, Any]]


_CN_MONTH_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}

_THIS_WEEK_TOKENS = ["本周", "这周", "本星期", "这星期", "这一周"]
_LAST_WEEK_TOKENS = ["上周", "上星期", "上一周"]
_WEEKDAY_INDEX_MAP = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_WORKWEEK_HINT_TOKENS = [
    "周一到周五",
    "周一至周五",
    "工作日",
    "工作周",
    "按工作日",
]
_AUTO_EXPAND_TOKENS = ("近90天", "近180天", "近365天", "全部历史")
_BASE_TEMPORAL_TOKENS = [
    "今天",
    "今日",
    "昨天",
    "昨日",
    "本周",
    "这周",
    "本星期",
    "这星期",
    "这一周",
    "上周",
    "上星期",
    "上一周",
    "本月",
    "这月",
    "这个月",
    "上月",
    "上个月",
    "近7天",
    "近30天",
    "最近7天",
    "最近30天",
    "近一个月",
    "最近一个月",
    "本学期",
    "这学期",
    "上学期",
    "开学以来",
    "今年",
    "去年",
]

TIME_MODE_LAST_30_DAYS = "last_30_days"
TIME_MODE_LAST_365_DAYS = "last_365_days"
TIME_MODE_ALL = "all"
TIME_MODE_SINCE_TERM_START = "since_term_start"
TIME_MODE_THIS_SEMESTER = "this_semester"

def get_workweek_hint_tokens() -> Tuple[str, ...]:
    """中文注释：实现get_workweek_hint_tokens的核心业务处理流程。"""
    return tuple(_WORKWEEK_HINT_TOKENS)


def get_temporal_tokens(*, include_workweek: bool = False) -> Tuple[str, ...]:
    """中文注释：实现get_temporal_tokens的核心业务处理流程。"""
    tokens = list(_BASE_TEMPORAL_TOKENS)
    if include_workweek:
        tokens.extend(_WORKWEEK_HINT_TOKENS)
    seen = set()
    out: List[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def build_sql_date_cast_expr(column_expr: str) -> str:
    """中文注释：实现build_sql_date_cast_expr的核心业务处理流程。"""
    expr = str(column_expr or "").strip()
    if not expr:
        return "NULL"
    return (
        "CASE "
        f"WHEN COALESCE(CAST({expr} AS text), '') ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
        f"THEN CAST(substr(CAST({expr} AS text), 1, 10) AS date) "
        "ELSE NULL END"
    )


def build_sql_coalesced_date_expr(*column_exprs: str, fallback_sql: Optional[str] = None) -> str:
    """中文注释：实现build_sql_coalesced_date_expr的核心业务处理流程。"""
    parts = [build_sql_date_cast_expr(expr) for expr in column_exprs if str(expr or "").strip()]
    if fallback_sql:
        parts.append(str(fallback_sql).strip())
    if not parts:
        return "NULL"
    return "COALESCE(\n  " + ",\n  ".join(parts) + "\n)"


def run_time_auto_expand(
    *,
    question: str,
    initial_rows: List[Any],
    initial_time_meta: Dict[str, Any],
    probe_query_fn: Callable[[str], Tuple[List[Any], Dict[str, Any]]],
    expand_tokens: Optional[Tuple[str, ...]] = None,
) -> TimeAutoExpandResult:
    """中文注释：实现run_time_auto_expand的核心业务处理流程。"""
    rows = list(initial_rows or [])
    time_meta = dict(initial_time_meta or {})
    steps: List[Dict[str, Any]] = []
    fallback_to_all_time = False

    should_expand = (not rows) and bool(time_meta.get("time_filter_default_applied"))
    if not should_expand:
        return TimeAutoExpandResult(
            rows=rows,
            time_meta=time_meta,
            fallback_to_all_time=False,
            auto_expand_applied=False,
            auto_expand_steps=steps,
        )

    tokens = expand_tokens or _AUTO_EXPAND_TOKENS
    for token in tokens:
        probe_rows, probe_meta = probe_query_fn(token)
        probe_rows = list(probe_rows or [])
        probe_meta = dict(probe_meta or {})
        steps.append(
            {
                "expand_token": token,
                "time_filter_mode": probe_meta.get("time_filter_mode"),
                "time_filter_start": probe_meta.get("time_filter_start"),
                "time_filter_end": probe_meta.get("time_filter_end"),
                "record_count": len(probe_rows),
            }
        )
        if probe_rows:
            rows = probe_rows
            time_meta = probe_meta
            fallback_to_all_time = bool(probe_meta.get("time_filter_mode") == "all")
            break
        if probe_meta.get("time_filter_mode") == "all":
            time_meta = probe_meta
            fallback_to_all_time = True

    return TimeAutoExpandResult(
        rows=rows,
        time_meta=time_meta,
        fallback_to_all_time=fallback_to_all_time,
        auto_expand_applied=True,
        auto_expand_steps=steps,
    )


def get_reference_today() -> date:
    """
    全域统一时间锚点：
    1. `QUERY_REFERENCE_DATE=YYYY-MM-DD` 时优先使用指定日期；
    2. 否则基于系统今天叠加 `QUERY_TIME_OFFSET_DAYS`（可为负数，-1 表示按昨天口径）。
    """
    raw_date = str(os.getenv("QUERY_REFERENCE_DATE", "") or "").strip()
    if raw_date:
        try:
            return datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            pass

    base = datetime.now().date()
    raw_offset = str(os.getenv("QUERY_TIME_OFFSET_DAYS", "0") or "0").strip()
    try:
        offset = int(raw_offset)
    except Exception:
        offset = 0
    if offset:
        return base + timedelta(days=offset)
    return base


def _normalize_school_name(value: str) -> str:
    """中文注释：封装_normalize_school_name的内部处理逻辑。"""
    text = str(value or "").strip()
    default_school = str(os.getenv("DEFAULT_SCHOOL_SCOPE", "") or "").strip()
    if default_school and default_school in text:
        return default_school
    return text


def _semester_school_name() -> str:
    """中文注释：封装_semester_school_name的内部处理逻辑。"""
    return _normalize_school_name(os.getenv("DEFAULT_SCHOOL_SCOPE", "").strip())


def _parse_date_value(value: Any) -> Optional[date]:
    """中文注释：封装_parse_date_value的内部处理逻辑。"""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    return None


def _normalize_cn_month_token(token: str) -> int:
    """中文注释：封装_normalize_cn_month_token的内部处理逻辑。"""
    text = str(token or "").strip()
    if not text:
        return 0
    if text.isdigit():
        try:
            month = int(text)
            return month if 1 <= month <= 12 else 0
        except Exception:
            return 0
    if text in _CN_MONTH_MAP:
        return _CN_MONTH_MAP[text]
    if text.startswith("十"):
        suffix = text[1:]
        if not suffix:
            return 10
        if suffix in _CN_MONTH_MAP:
            month = 10 + _CN_MONTH_MAP[suffix]
            return month if 1 <= month <= 12 else 0
    return 0


def _month_range(year: int, month: int) -> Optional[Tuple[date, date]]:
    """中文注释：封装_month_range的内部处理逻辑。"""
    if not (1 <= int(month) <= 12):
        return None
    start = date(int(year), int(month), 1)
    if int(month) == 12:
        end = date(int(year) + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(int(year), int(month) + 1, 1) - timedelta(days=1)
    return start, end


def _parse_month_specific_range(question: str, today: date) -> Optional[TimeRange]:
    """中文注释：封装_parse_month_specific_range的内部处理逻辑。"""
    q = question or ""
    # 年-月（例如 2026年2月 / 2026年二月）
    m = re.search(r"(20\d{2})\s*年\s*([0-9一二三四五六七八九十]{1,3})\s*月(?:份)?", q)
    if m:
        month = _normalize_cn_month_token(m.group(2))
        month_span = _month_range(int(m.group(1)), month)
        if month_span:
            start, end = month_span
            return TimeRange(mode="explicit_month", start=start, end=end)

    # 相对年份 + 月（例如 今年二月 / 去年2月）
    m = re.search(r"(今年|去年)\s*([0-9一二三四五六七八九十]{1,3})\s*月(?:份)?", q)
    if m:
        month = _normalize_cn_month_token(m.group(2))
        base_year = today.year if m.group(1) == "今年" else today.year - 1
        month_span = _month_range(base_year, month)
        if month_span:
            start, end = month_span
            return TimeRange(mode="relative_year_month", start=start, end=end)

    # 裸月（例如 2月 / 二月），默认取最近一次出现该月份的自然月
    m = re.search(r"(?<!\d)([0-9一二三四五六七八九十]{1,3})\s*月(?:份)?(?!\d)", q)
    if m:
        month = _normalize_cn_month_token(m.group(1))
        if month:
            year = today.year
            if month > today.month:
                year -= 1
            month_span = _month_range(year, month)
            if month_span:
                start, end = month_span
                return TimeRange(mode="explicit_month_inferred_year", start=start, end=end)
    return None


def prefers_workweek(question: str) -> bool:
    """中文注释：实现prefers_workweek的核心业务处理流程。"""
    q = str(question or "")
    return any(token in q for token in _WORKWEEK_HINT_TOKENS)


def get_week_range(
    *,
    today: Optional[date] = None,
    offset_weeks: int = 0,
    weekdays_only: bool = False,
    school_name: Optional[str] = None,
) -> Tuple[date, date]:
    """中文注释：实现get_week_range的核心业务处理流程。"""
    target_day = today or get_reference_today()

    configured = _resolve_configured_week_range(
        target_day,
        offset_weeks=offset_weeks,
        school_name=school_name,
    )
    if configured:
        start, end = configured
        if weekdays_only:
            workweek_end = min(start + timedelta(days=4), end)
            if offset_weeks == 0:
                workweek_end = min(workweek_end, target_day)
            return start, workweek_end
        if offset_weeks == 0:
            return start, min(end, target_day)
        return start, end

    current_week_start = target_day - timedelta(days=target_day.weekday())
    start = current_week_start + timedelta(days=offset_weeks * 7)
    if weekdays_only:
        end = start + timedelta(days=4)
        if offset_weeks == 0:
            end = min(end, target_day)
        return start, end
    if offset_weeks == 0:
        return start, target_day
    return start, start + timedelta(days=6)


def resolve_week_range(question: str, today: Optional[date] = None) -> Optional[TimeRange]:
    """中文注释：实现resolve_week_range的核心业务处理流程。"""
    q = question or ""
    target_day = today or get_reference_today()
    weekdays_only = prefers_workweek(q)
    if any(k in q for k in _THIS_WEEK_TOKENS):
        start, end = get_week_range(today=target_day, offset_weeks=0, weekdays_only=weekdays_only)
        return TimeRange(mode="this_workweek" if weekdays_only else "this_week", start=start, end=end)
    if any(k in q for k in _LAST_WEEK_TOKENS):
        start, end = get_week_range(today=target_day, offset_weeks=-1, weekdays_only=weekdays_only)
        return TimeRange(mode="last_workweek" if weekdays_only else "last_week", start=start, end=end)
    return None


def _resolve_weekday_specific_range(question: str, today: Optional[date] = None) -> Optional[TimeRange]:
    """解析“上周五/本周三/这周一”这类单日问法，避免被扩成整周范围。"""
    q = str(question or "")
    if not q:
        return None

    # 仅对带“上周/本周/这周/上星期/本星期/这星期”前缀的问法生效，避免裸“周五”歧义。
    # 避免把“上周六年级/本周一年级”误判为“上周六/本周一”单日口径。
    m = re.search(
        r"(上周|本周|这周|上星期|本星期|这星期)\s*(?:周|星期)?\s*([一二三四五六日天])(?!年级|年|班)",
        q,
    )
    if not m:
        return None

    prefix = str(m.group(1) or "")
    weekday = str(m.group(2) or "")
    if weekday not in _WEEKDAY_INDEX_MAP:
        return None

    target_day = today or get_reference_today()
    offset_weeks = -1 if prefix in {"上周", "上星期"} else 0

    # 显式“上周五/本周三”按自然周定位，不截断到“截至今天”。
    configured = _resolve_configured_week_range(
        target_day,
        offset_weeks=offset_weeks,
        school_name=None,
    )
    if configured:
        week_start, week_end = configured
    else:
        current_week_start = target_day - timedelta(days=target_day.weekday())
        week_start = current_week_start + timedelta(days=offset_weeks * 7)
        week_end = week_start + timedelta(days=6)
    day = week_start + timedelta(days=_WEEKDAY_INDEX_MAP[weekday])

    # 配置周边界保护，防止越界到相邻周。
    if day < week_start:
        day = week_start
    if day > week_end:
        day = week_end

    mode = "last_week_weekday" if offset_weeks < 0 else "this_week_weekday"
    return TimeRange(mode=mode, start=day, end=day)


def _load_semester_terms(school_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """中文注释：封装_load_semester_terms的内部处理逻辑。"""
    del school_name
    return []


def _load_semester_weeks(school_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """中文注释：封装_load_semester_weeks的内部处理逻辑。"""
    del school_name
    return []


def _resolve_configured_week_range(
    target_day: date,
    *,
    offset_weeks: int = 0,
    school_name: Optional[str] = None,
) -> Optional[Tuple[date, date]]:
    """中文注释：封装_resolve_configured_week_range的内部处理逻辑。"""
    weeks = _load_semester_weeks(school_name)
    if not weeks:
        return None

    current_idx: Optional[int] = None
    for idx, item in enumerate(weeks):
        if item["start"] <= target_day <= item["end"]:
            current_idx = idx
            break
    if current_idx is None:
        past_indices = [i for i, item in enumerate(weeks) if item["start"] <= target_day]
        current_idx = past_indices[-1] if past_indices else 0

    target_idx = current_idx + int(offset_weeks)
    if target_idx < 0 or target_idx >= len(weeks):
        return None

    picked = weeks[target_idx]
    return picked["start"], picked["end"]


def _load_current_semester_scope(school_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """中文注释：封装_load_current_semester_scope的内部处理逻辑。"""
    del school_name
    return None


def _fallback_current_semester(today: date) -> Tuple[date, date, str]:
    """中文注释：封装_fallback_current_semester的内部处理逻辑。"""
    y = today.year
    m = today.month
    if m == 1:
        return date(y - 1, 9, 1), date(y, 1, 31), f"{y-1}学年第一学期"
    if 2 <= m <= 8:
        return date(y, 2, 1), date(y, 8, 31), f"{y-1}学年第二学期"
    return date(y, 9, 1), date(y + 1, 1, 31), f"{y}学年第一学期"


def _fallback_previous_semester(today: date) -> Tuple[date, date, str]:
    """中文注释：封装_fallback_previous_semester的内部处理逻辑。"""
    cur_start, _, cur_label = _fallback_current_semester(today)
    if "第二学期" in cur_label:
        y = cur_start.year - 1
        return date(y, 9, 1), date(y + 1, 1, 31), f"{y}学年第一学期"
    if cur_start.month == 9:
        y = cur_start.year
        return date(y, 2, 1), date(y, 8, 31), f"{y-1}学年第二学期"
    y = cur_start.year - 1
    return date(y, 2, 1), date(y, 8, 31), f"{y-1}学年第二学期"


def get_current_semester_range(today: Optional[date] = None, school_name: Optional[str] = None) -> Tuple[date, date, str]:
    """中文注释：实现get_current_semester_range的核心业务处理流程。"""
    target_day = today or get_reference_today()

    # 口径优先级：学校 schema 校历 > 规则兜底；旧全局校历表已下线。
    terms = _load_semester_terms(school_name)
    if terms:
        current = [t for t in terms if t["start"] <= target_day <= t["end"]]
        if current:
            picked = sorted(current, key=lambda item: (item["start"], item["end"]))[-1]
            return picked["start"], picked["end"], picked["term_name"]
        past = [t for t in terms if t["start"] <= target_day]
        if past:
            picked = sorted(past, key=lambda item: (item["start"], item["end"]))[-1]
            return picked["start"], picked["end"], picked["term_name"]
        picked = sorted(terms, key=lambda item: (item["start"], item["end"]))[0]
        return picked["start"], picked["end"], picked["term_name"]

    current_scope = _load_current_semester_scope(school_name)
    if current_scope:
        return current_scope["start"], current_scope["end"], current_scope["term_name"]

    return _fallback_current_semester(target_day)


def get_previous_semester_range(today: Optional[date] = None, school_name: Optional[str] = None) -> Tuple[date, date, str]:
    """中文注释：实现get_previous_semester_range的核心业务处理流程。"""
    target_day = today or get_reference_today()
    terms = _load_semester_terms(school_name)
    if terms:
        ordered = sorted(terms, key=lambda item: (item["start"], item["end"]))
        current_start, current_end, current_label = get_current_semester_range(target_day, school_name)
        for idx, item in enumerate(ordered):
            if item["start"] == current_start and item["end"] == current_end and item["term_name"] == current_label:
                if idx > 0:
                    prev = ordered[idx - 1]
                    return prev["start"], prev["end"], prev["term_name"]
                break
        past = [t for t in ordered if t["end"] < target_day]
        if past:
            prev = past[-1]
            return prev["start"], prev["end"], prev["term_name"]
    return _fallback_previous_semester(target_day)


def resolve_named_semester_range(term_name: str, today: Optional[date] = None, school_name: Optional[str] = None) -> Optional[Tuple[date, date, str]]:
    """中文注释：实现resolve_named_semester_range的核心业务处理流程。"""
    normalized = re.sub(r"\s+", "", str(term_name or ""))
    if not normalized:
        return None
    terms = _load_semester_terms(school_name)
    for item in terms:
        if re.sub(r"\s+", "", item["term_name"]) == normalized:
            return item["start"], item["end"], item["term_name"]

    m = re.search(r"(20\d{2})\s*学年\s*第?\s*([一二12])\s*学期", normalized)
    if not m:
        return None
    year = int(m.group(1))
    sem = m.group(2)
    if sem in {"一", "1"}:
        return date(year, 9, 1), date(year + 1, 1, 31), f"{year}学年第一学期"
    return date(year + 1, 2, 1), date(year + 1, 8, 31), f"{year}学年第二学期"


def resolve_semester_range(question: str, today: Optional[date] = None, school_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """中文注释：实现resolve_semester_range的核心业务处理流程。"""
    q = question or ""
    target_day = today or datetime.now().date()
    m = re.search(r"(20\d{2}\s*学年\s*第?\s*[一二12]\s*学期)", q)
    if m:
        named = resolve_named_semester_range(m.group(1), today=target_day, school_name=school_name)
        if named:
            start, end, label = named
            return {"mode": "named_semester", "start": start, "end": end, "label": label}

    if "开学以来" in q:
        start, end, label = get_current_semester_range(target_day, school_name)
        return {"mode": "since_term_start", "start": start, "end": min(target_day, end), "label": label}
    if any(k in q for k in ["本学期", "这学期"]):
        start, end, label = get_current_semester_range(target_day, school_name)
        return {"mode": "this_semester", "start": start, "end": end, "label": label}
    if any(k in q for k in ["上学期", "上一学期"]):
        start, end, label = get_previous_semester_range(target_day, school_name)
        return {"mode": "last_semester", "start": start, "end": end, "label": label}
    return None


def parse_time_range(question: str, today: Optional[date] = None) -> TimeRange:
    """中文注释：实现parse_time_range的核心业务处理流程。"""
    q = question or ""
    target_day = today or get_reference_today()

    semester_range = resolve_semester_range(q, today=target_day)
    if semester_range:
        return TimeRange(
            mode=str(semester_range.get("mode") or "semester"),
            start=semester_range.get("start"),
            end=semester_range.get("end"),
        )

    # 双周对比口径（本周 vs 上周）：返回覆盖上周周一到本周结束（截至今天）的窗口，
    # 后续由查询层按周分组并补齐缺失周。
    has_this_week = any(k in q for k in _THIS_WEEK_TOKENS)
    has_last_week = any(k in q for k in _LAST_WEEK_TOKENS)
    if has_this_week and has_last_week:
        last_week_start, _last_week_end = get_week_range(today=target_day, offset_weeks=-1, weekdays_only=False)
        _this_week_start, this_week_end = get_week_range(today=target_day, offset_weeks=0, weekdays_only=False)
        return TimeRange(mode="this_vs_last_week", start=last_week_start, end=this_week_end)

    # 双月对比口径（本月 vs 上月）：返回覆盖上月首日到本月末日的窗口，
    # 后续由查询层按月份分组并补齐缺失月份。
    has_this_month = any(k in q for k in ["本月", "这月"])
    has_last_month = any(k in q for k in ["上个月", "上月"])
    if has_this_month and has_last_month:
        this_month_start = target_day.replace(day=1)
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        if this_month_start.month == 12:
            this_month_end = date(this_month_start.year + 1, 1, 1) - timedelta(days=1)
        else:
            this_month_end = date(this_month_start.year, this_month_start.month + 1, 1) - timedelta(days=1)
        return TimeRange(mode="this_vs_last_month", start=last_month_start, end=this_month_end)

    month_range = _parse_month_specific_range(q, target_day)
    if month_range:
        return month_range

    if "去年" in q:
        start = date(target_day.year - 1, 1, 1)
        end = date(target_day.year - 1, 12, 31)
        return TimeRange(mode="last_year", start=start, end=end)
    if "今年" in q:
        start = date(target_day.year, 1, 1)
        end = date(target_day.year, 12, 31)
        return TimeRange(mode="this_year", start=start, end=end)

    today_tokens = ["今天", "今日"]
    yesterday_tokens = ["昨天", "昨日"]
    last_today_idx = max((q.rfind(tok) for tok in today_tokens), default=-1)
    last_yesterday_idx = max((q.rfind(tok) for tok in yesterday_tokens), default=-1)
    if last_today_idx >= 0 or last_yesterday_idx >= 0:
        if last_yesterday_idx > last_today_idx:
            d = target_day - timedelta(days=1)
            return TimeRange(mode="yesterday", start=d, end=d)
        return TimeRange(mode="today", start=target_day, end=target_day)
    weekday_range = _resolve_weekday_specific_range(q, today=target_day)
    if weekday_range:
        return weekday_range
    week_range = resolve_week_range(q, today=target_day)
    if week_range:
        return week_range
    if any(k in q for k in ["上个月", "上月"]):
        this_month_start = target_day.replace(day=1)
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return TimeRange(mode="last_month", start=last_month_start, end=last_month_end)
    if any(k in q for k in ["本月", "这月"]):
        start = target_day.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        return TimeRange(mode="this_month", start=start, end=end)

    if any(k in q for k in ["近一周", "最近一周", "近7天", "最近7天", "过去7天"]):
        start = target_day - timedelta(days=6)
        return TimeRange(mode="last_7_days", start=start, end=target_day)

    if any(k in q for k in ["一个月", "近一月", "近一个月", "最近一月", "最近一个月"]):
        start = target_day - timedelta(days=29)
        return TimeRange(mode="last_30_days", start=start, end=target_day)

    if any(k in q for k in ["近半年", "最近半年", "过去半年", "这半年", "半年来"]):
        start = target_day - timedelta(days=179)
        return TimeRange(mode="last_180_days", start=start, end=target_day)

    if any(k in q for k in ["近一年", "最近一年", "过去一年", "这一年", "一年来", "近1年", "最近1年"]):
        start = target_day - timedelta(days=364)
        return TimeRange(mode="last_365_days", start=start, end=target_day)

    m = re.search(r"(?:近|最近)(\d{1,2})个?月", q)
    if m:
        months = max(1, int(m.group(1)))
        days = min(3650, months * 30)
        start = target_day - timedelta(days=days - 1)
        return TimeRange(mode=f"last_{days}_days", start=start, end=target_day)

    m = re.search(r"(?:近|最近)(\d{1,2})年", q)
    if m:
        years = max(1, int(m.group(1)))
        days = min(3650, years * 365)
        start = target_day - timedelta(days=days - 1)
        return TimeRange(mode=f"last_{days}_days", start=start, end=target_day)

    m = re.search(r"近(\d{1,3})天", q)
    if m:
        days = max(1, int(m.group(1)))
        start = target_day - timedelta(days=days - 1)
        return TimeRange(mode=f"last_{days}_days", start=start, end=target_day)

    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})\s*(?:到|至|~|-)\s*(\d{4}-\d{1,2}-\d{1,2})", q)
    if m:
        try:
            s = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            e = datetime.strptime(m.group(2), "%Y-%m-%d").date()
            if s <= e:
                return TimeRange(mode="explicit_range", start=s, end=e)
        except Exception:
            pass

    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?", q)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return TimeRange(mode="explicit_day", start=d, end=d)
        except Exception:
            pass

    return TimeRange(mode="all", start=None, end=None)


def _should_force_all(question: str) -> bool:
    """中文注释：封装_should_force_all的内部处理逻辑。"""
    q = question or ""
    force_all_tokens = ["全部", "所有", "历史", "累计", "全量", "历年", "至今", "长期"]
    return any(t in q for t in force_all_tokens)


def _normalize_default_mode_for_school_calendar(mode: str) -> str:
    """全域默认时间口径统一：旧的滚动窗口默认值收敛到学校学期/周配置。"""
    raw = str(mode or "").strip().lower()
    if not raw:
        return raw

    policy = str(os.getenv("SCHOOL_CALENDAR_DEFAULT_POLICY", "1") or "1").strip().lower()
    if policy in {"0", "false", "off", "no"}:
        return raw

    if raw in {"last_7_days"}:
        return "this_week"
    if raw in {"last_30_days", "last_180_days", "last_365_days"}:
        return "this_semester"
    return raw


def _default_time_range(mode: str, today: date) -> TimeRange:
    """中文注释：封装_default_time_range的内部处理逻辑。"""
    m = (mode or "").strip().lower()
    if not m or m == "all":
        return TimeRange(mode="all", start=None, end=None)
    if m == "today":
        return TimeRange(mode="today", start=today, end=today)
    if m == "yesterday":
        d = today - timedelta(days=1)
        return TimeRange(mode="yesterday", start=d, end=d)
    if m == "this_week":
        start, end = get_week_range(today=today, offset_weeks=0, weekdays_only=False)
        return TimeRange(mode="this_week", start=start, end=end)
    if m == "last_week":
        start, end = get_week_range(today=today, offset_weeks=-1, weekdays_only=False)
        return TimeRange(mode="last_week", start=start, end=end)
    if m == "this_workweek":
        start, end = get_week_range(today=today, offset_weeks=0, weekdays_only=True)
        return TimeRange(mode="this_workweek", start=start, end=end)
    if m == "last_workweek":
        start, end = get_week_range(today=today, offset_weeks=-1, weekdays_only=True)
        return TimeRange(mode="last_workweek", start=start, end=end)
    if m in {"this_semester", "current_semester"}:
        start, end, _label = get_current_semester_range(today, _semester_school_name())
        return TimeRange(mode="this_semester", start=start, end=end)
    if m == "since_term_start":
        start, end, _label = get_current_semester_range(today, _semester_school_name())
        return TimeRange(mode="since_term_start", start=start, end=min(today, end))
    if m == "last_semester":
        start, end, _label = get_previous_semester_range(today, _semester_school_name())
        return TimeRange(mode="last_semester", start=start, end=end)
    if m == "this_month":
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        return TimeRange(mode="this_month", start=start, end=end)
    if m == "last_month":
        this_month_start = today.replace(day=1)
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return TimeRange(mode="last_month", start=last_month_start, end=last_month_end)
    m_days = re.match(r"last_(\d{1,3})_days", m)
    if m_days:
        days = max(1, int(m_days.group(1)))
        start = today - timedelta(days=days - 1)
        return TimeRange(mode=f"last_{days}_days", start=start, end=today)
    return TimeRange(mode="all", start=None, end=None)


def build_time_filter(
    question: str,
    date_expr_sql: str,
    default_mode: Optional[str] = None,
) -> Tuple[Optional[str], list, Dict[str, Any]]:
    """中文注释：实现build_time_filter的核心业务处理流程。"""
    anchor_today = get_reference_today()
    tr = parse_time_range(question, today=anchor_today)
    default_applied = False
    if tr.mode == "all" and not _should_force_all(question):
        fallback = (default_mode or "").strip() or os.getenv("QUERY_DEFAULT_TIME_MODE", "").strip()
        fallback = _normalize_default_mode_for_school_calendar(fallback)
        if fallback:
            dt = _default_time_range(fallback, anchor_today)
            if dt.start and dt.end:
                tr = dt
                default_applied = True

    meta: Dict[str, Any] = {
        "time_filter_mode": tr.mode,
        "time_filter_start": tr.start.isoformat() if tr.start else None,
        "time_filter_end": tr.end.isoformat() if tr.end else None,
        "time_filter_default_applied": default_applied,
    }
    if tr.start and tr.end:
        return f"({date_expr_sql}) BETWEEN %s AND %s", [tr.start.isoformat(), tr.end.isoformat()], meta
    return None, [], meta
