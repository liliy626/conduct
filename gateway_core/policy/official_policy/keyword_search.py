from __future__ import annotations

import re
from typing import Any


def keyword_sql(
    *,
    safe_schema: str,
    safe_table: str,
    safe_page_table: str,
    table_ref: str,
    id_col: str,
    title_col: str,
    chunk_col: str,
    url_col: str,
    source_col: str,
    chunk_id_col: str,
    page_id_col: str,
    official_join: bool,
    keyword_count: int,
    limit_value: int,
    page_date_filter: str = "",
) -> str:
    score_expr = _keyword_score_sql(
        title_expr='COALESCE(p."source_title", \'\') || \' \' || COALESCE(c."section_title", \'\')'
        if official_join
        else f"COALESCE({title_col}, '')",
        chunk_expr=f"COALESCE(c.{chunk_col}, '')" if official_join else f"COALESCE({chunk_col}, '')",
        source_expr='COALESCE(p."org_name", \'\') || \' \' || COALESCE(p."source_domain", \'\')'
        if official_join
        else f"COALESCE({source_col}, '')",
        keyword_count=keyword_count,
    )
    if official_join:
        page_ref = f"{safe_schema}.{safe_page_table}" if safe_page_table else ""
        page_join = f"LEFT JOIN {page_ref} p ON p.{page_id_col} = c.{page_id_col}" if page_ref else ""
        topic_ref = f'{safe_schema}."policy_chunk_topic_tags"'
        conditions = " OR ".join(
            [
                "(COALESCE(p.\"source_title\", '') ILIKE %s OR COALESCE(c.\"section_title\", '') ILIKE %s "
                f"OR COALESCE(c.{chunk_col}, '') ILIKE %s OR COALESCE(t.\"topic_key\"::text, '') ILIKE %s "
                "OR COALESCE(t.\"evidence\"::text, '') ILIKE %s)"
                for _ in range(keyword_count)
            ]
        )
        return f"""
            SELECT c.{chunk_id_col},
                   COALESCE(p."source_title", c."section_title", '') AS title,
                   c.{chunk_col},
                   COALESCE(p."canonical_url", p."source_url", '') AS official_url,
                   COALESCE(p."org_name", p."source_domain", '') AS source_name,
                   0::float AS similarity,
                   (COALESCE(MAX(t."match_weight"), 0)::float + MAX({score_expr})) AS topic_weight,
                   p."published_at" AS published_at
            FROM {table_ref} c
            {page_join}
            LEFT JOIN {topic_ref} t ON t.{chunk_id_col} = c.{chunk_id_col}
            WHERE ({conditions})
            {page_date_filter}
            GROUP BY c.{chunk_id_col}, title, c.{chunk_col}, official_url, source_name, p."published_at"
            ORDER BY topic_weight DESC, p."published_at" DESC NULLS LAST, c.{chunk_id_col}
            LIMIT {int(limit_value)}
        """

    conditions = " OR ".join(
        [
            f"(COALESCE({title_col}, '') ILIKE %s OR COALESCE({chunk_col}, '') ILIKE %s OR COALESCE({source_col}, '') ILIKE %s)"
            for _ in range(keyword_count)
        ]
    )
    return f"""
        SELECT {id_col}, {title_col}, {chunk_col}, {url_col}, {source_col},
               0::float AS similarity,
               ({score_expr})::float AS topic_weight
        FROM {table_ref}
        WHERE ({conditions})
        ORDER BY topic_weight DESC
        LIMIT {int(limit_value)}
    """


def keyword_params(
    keywords: list[str],
    *,
    official_join: bool,
    limit_value: int,
    page_date_params: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    del limit_value
    params: list[Any] = []
    for keyword in keywords:
        params.extend([f"%{keyword}%"] * 3)
    repeat = 5 if official_join else 3
    for keyword in keywords:
        params.extend([f"%{keyword}%"] * repeat)
    params.extend(page_date_params)
    return tuple(params)


def row_to_candidate(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0] or ""),
        "title": str(row[1] or ""),
        "chunk": str(row[2] or ""),
        "official_url": str(row[3] or ""),
        "source_name": str(row[4] or ""),
        "similarity": float(row[5] or 0.0) if len(row) > 5 else 0.0,
        "topic_weight": float(row[6] or 0.0) if len(row) > 6 else 0.0,
        "published_at": row[7] if len(row) > 7 else "",
    }


def page_date_filter(*, published_start: str, published_end: str) -> str:
    clauses: list[str] = []
    if str(published_start or "").strip():
        clauses.append('p."published_at" >= %s::date')
    if str(published_end or "").strip():
        clauses.append('p."published_at" <= %s::date')
    return ("AND " + " AND ".join(clauses)) if clauses else ""


def page_date_params(*, published_start: str, published_end: str) -> tuple[Any, ...]:
    params: list[Any] = []
    if str(published_start or "").strip():
        params.append(str(published_start).strip())
    if str(published_end or "").strip():
        params.append(str(published_end).strip())
    return tuple(params)


def extract_keywords(question: str) -> list[str]:
    text = str(question or "").strip()
    known_terms = [
        "浦东教发院",
        "教育发展学院",
        "教发院",
        "课后服务",
        "教师培训",
        "师训通知",
        "十四五",
        "校本研修",
        "德育培训",
        "教研活动",
        "课题研究",
        "科研管理",
        "公开课",
        "研讨会",
        "课例研究",
        "人工智能",
        "教师专业发展学校",
        "官方网页",
        "链接",
        "网址",
        "下载",
        "附件",
        "通知",
        "高级教师",
        "正高级",
        "园丁奖",
        "职称",
        "评审",
        "一票否决",
        "免论文",
        "班主任",
        "教龄",
        "荣誉",
        "业绩",
        "材料",
        "申报",
        "年限",
        "条件",
        "区教育局",
        "组织",
        "主体",
        "加分",
        "优先",
    ]
    out: list[str] = []
    clean_text = re.sub(r"[^\w\s\u4e00-\u9fff]", "", text)
    solid_text = re.sub(r"\s+", "", clean_text)
    for term in known_terms:
        if term in text and term not in out:
            out.append(term)
    for phrase in _policy_phrase_ngrams(solid_text):
        if phrase not in out:
            out.append(phrase)
    for token in re.split(r"[\s,，。？?、/|；;:：()（）]+", text):
        clean = token.strip()
        if 2 <= len(clean) <= 20 and clean not in out:
            out.append(clean)
    return out[:40]


def keyword_score(*, keywords: list[str], title: str, chunk: str, source_name: str, question: str = "") -> float:
    if not keywords:
        return 0.0
    title_text = title.lower()
    chunk_text = chunk.lower()
    source_text = source_name.lower()
    question_text = question.lower()
    score = 0.0
    for keyword in keywords:
        key = keyword.lower()
        if len(key) >= 4 and key in title_text:
            score += 0.45
        if key in title_text:
            score += 0.25
        if key in chunk_text:
            score += 0.08
        if key in source_text:
            score += 0.06

    for terms, bonus in _cross_match_rules():
        if not all(term in question_text for term in terms):
            continue
        if all((term in title_text or term in chunk_text or term in source_text) for term in terms):
            score += bonus
            if all(term in title_text for term in terms):
                score += 0.40

    if any(signal in question_text for signal in ("链接", "网址", "在哪看", "下载", "附件")):
        if any(signal in title_text or signal in chunk_text for signal in ("链接", "网址", "附件", "下载", "通知")):
            score += 0.12
    return score


def _keyword_score_sql(*, title_expr: str, chunk_expr: str, source_expr: str, keyword_count: int) -> str:
    parts = []
    for _ in range(keyword_count):
        parts.append(
            f"""
            (CASE WHEN {title_expr} ILIKE %s THEN 8 ELSE 0 END
             + CASE WHEN {chunk_expr} ILIKE %s THEN 3 ELSE 0 END
             + CASE WHEN {source_expr} ILIKE %s THEN 1 ELSE 0 END)
            """
        )
    return " + ".join(parts) if parts else "0"


def _policy_phrase_ngrams(text: str, *, min_n: int = 4, max_n: int = 8) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for n in range(min_n, max_n + 1):
        for i in range(0, max(len(text) - n + 1, 0)):
            phrase = text[i : i + n]
            if _useful_policy_ngram(phrase):
                out.append(phrase)
    return out[:80]


def _useful_policy_ngram(phrase: str) -> bool:
    signals = (
        "教发院",
        "教育发展",
        "课后",
        "服务",
        "教师",
        "培训",
        "教研",
        "课题",
        "科研",
        "通知",
        "公开课",
        "研讨",
        "链接",
        "十四五",
    )
    return any(signal in phrase for signal in signals)


def _cross_match_rules() -> list[tuple[tuple[str, ...], float]]:
    return [
        (("课后服务", "教师培训"), 0.60),
        (("浦东教发院", "教师培训"), 0.40),
        (("教发院", "教师培训"), 0.30),
        (("课题", "研究"), 0.25),
        (("师训", "通知"), 0.20),
        (("教师专业发展学校", "通知"), 0.30),
    ]
