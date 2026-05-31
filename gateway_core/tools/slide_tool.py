from __future__ import annotations

import time
import json
import os
import zipfile
from urllib import request as urllib_request
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifact_store import artifact_download_url, safe_artifact_path
from .privacy import contains_sensitive_context
from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


class SlideTool(AgentTool):
    name = "slide"
    description = "Builds a simple PPTX report from local evidence, chart artifacts, and structured sections."

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        blocked, reason = contains_sensitive_context({"external_context": tool_input.arguments.get("external_context")})
        if blocked:
            return _output(
                started,
                ok=False,
                warnings=[f"privacy block: {reason}; slide generation only accepts local evidence"],
                error="sensitive external context cannot be sent to slide generation",
            )

        title = str(tool_input.arguments.get("title") or "汇报材料").strip()
        sections = _sections(tool_input)
        charts = [item for item in tool_input.arguments.get("charts") or [] if isinstance(item, dict)]
        evidence_by_task = tool_input.arguments.get("evidence_by_task") if isinstance(tool_input.arguments.get("evidence_by_task"), dict) else {}
        slides = _build_slides(title=title, sections=sections, charts=charts, evidence_by_task=evidence_by_task)
        if not slides:
            return _output(started, ok=False, error="slide tool requires at least one slide section")

        provider_name = _slide_provider_name()
        if provider_name in {"llm", "qwen"}:
            llm_output = _run_llm_provider(
                started=started,
                title=title,
                slides=slides,
                charts=charts,
                evidence_by_task=evidence_by_task,
                context=context,
            )
            if llm_output.ok:
                return llm_output
            if not _llm_fallback_enabled():
                return llm_output

        artifacts = _write_deck_artifacts(
            tenant_id=context.tenant_id,
            title=title,
            slides=slides,
            provider="basic",
            model="",
            evidence_by_task=evidence_by_task,
            charts=charts,
        )
        warnings = []
        if provider_name in {"llm", "qwen"}:
            warnings.append("llm slide provider unavailable; fell back to basic pptx generator")
        elif provider_name != "basic":
            warnings.append(f"unsupported slide provider {provider_name!r}; fell back to basic pptx generator")
        return _output(
            started,
            ok=True,
            evidence=[{"kind": "slide_outline", "slide_count": len(slides)}],
            artifacts=artifacts,
            warnings=warnings,
        )


def _sections(tool_input: AgentToolInput) -> List[Dict[str, Any]]:
    raw = tool_input.arguments.get("sections")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _build_slides(
    *,
    title: str,
    sections: List[Dict[str, Any]],
    charts: List[Dict[str, Any]],
    evidence_by_task: Dict[str, Any],
) -> List[Dict[str, Any]]:
    slides: List[Dict[str, Any]] = [{"title": title, "bullets": ["基于当前网关证据自动生成", "请结合 trace 检查数据口径"]}]
    for section in sections[:8]:
        sec_title = str(section.get("title") or section.get("type") or "内容").strip()
        bullets = _bullet_list(section.get("content") or section.get("bullets") or [])
        if bullets:
            slides.append({"title": sec_title, "bullets": bullets[:8]})

    for chart in charts[:4]:
        chart_title = str(chart.get("title") or "数据图表").strip()
        data = chart.get("data")
        bullets = _chart_bullets(data)
        slides.append({"title": chart_title, "bullets": bullets or ["图表数据已写入网关 artifact"]})

    if evidence_by_task:
        slides.append({"title": "数据证据概览", "bullets": _evidence_bullets(evidence_by_task)})

    return slides[:12]


def _slide_provider_name() -> str:
    return str(os.getenv("GATEWAY_SLIDE_PROVIDER", "basic") or "basic").strip().lower()


def _llm_fallback_enabled() -> bool:
    return str(os.getenv("GATEWAY_SLIDE_LLM_FALLBACK_TO_BASIC", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _run_llm_provider(
    *,
    started: float,
    title: str,
    slides: List[Dict[str, Any]],
    charts: List[Dict[str, Any]],
    evidence_by_task: Dict[str, Any],
    context: ToolExecutionContext,
) -> AgentToolOutput:
    config = _llm_slide_config()
    if not config["base_url"]:
        return _output(started, ok=False, error="GATEWAY_SLIDE_LLM_BASE_URL is required for llm slide provider")
    if not config["api_key"]:
        return _output(started, ok=False, error=f"missing env var: {config['api_key_env']}")
    try:
        generated = _generate_llm_slide_outline(
            config=config,
            title=title,
            slides=slides,
            charts=charts,
            evidence_by_task=evidence_by_task,
        )
        generated_slides = _sanitize_llm_slides(
            generated,
            fallback_slides=slides,
            fallback_title=title,
            max_slides=int(config["max_slides"]),
        )
        deck_title = str(generated.get("title") or title)
        artifacts = _write_deck_artifacts(
            tenant_id=context.tenant_id,
            title=deck_title,
            slides=generated_slides,
            provider="qwen_llm",
            model=config["model"],
            evidence_by_task=evidence_by_task,
            charts=charts,
        )
        return _output(
            started,
            ok=True,
            evidence=[
                {
                    "kind": "slide_outline",
                    "slide_count": len(generated_slides),
                    "provider": "qwen_llm",
                    "model": config["model"],
                }
            ],
            artifacts=artifacts,
        )
    except Exception as exc:
        return _output(started, ok=False, error=f"llm slide provider failed: {exc}")


def _llm_slide_config() -> Dict[str, Any]:
    model_id = os.getenv("GATEWAY_SLIDE_LLM_MODEL_ID", "yili-qwen").strip() or "yili-qwen"
    spec = _load_model_config_spec(model_id)
    base_url = os.getenv("GATEWAY_SLIDE_LLM_BASE_URL", "").strip() or str(spec.get("base_url") or "")
    model = os.getenv("GATEWAY_SLIDE_LLM_MODEL", "").strip() or str(spec.get("model") or "qwen3.5-plus")
    api_key_env = os.getenv("GATEWAY_SLIDE_LLM_API_KEY_ENV", "").strip() or str(spec.get("api_key_env") or "DASHSCOPE_API_KEY")
    return {
        "base_url": base_url.rstrip("/"),
        "model": model,
        "api_key_env": api_key_env,
        "api_key": os.getenv(api_key_env, "").strip(),
        "timeout_sec": _float_env("GATEWAY_SLIDE_LLM_TIMEOUT_SEC", float(spec.get("timeout") or 60.0)),
        "temperature": _float_env("GATEWAY_SLIDE_LLM_TEMPERATURE", float(spec.get("temperature") or 0.2)),
        "max_tokens": _int_env("GATEWAY_SLIDE_LLM_MAX_TOKENS", 2200),
        "max_slides": _int_env("GATEWAY_SLIDE_LLM_MAX_SLIDES", 10),
    }


def _load_model_config_spec(model_id: str) -> Dict[str, Any]:
    path = Path(os.getenv("MODEL_CONFIG_PATH", Path.cwd() / "model_config.yaml"))
    if not path.exists():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    for item in raw.get("models") or []:
        if str(item.get("id") or "").strip() == model_id:
            return dict(item)
    return {}


def _generate_llm_slide_outline(
    *,
    config: Dict[str, Any],
    title: str,
    slides: List[Dict[str, Any]],
    charts: List[Dict[str, Any]],
    evidence_by_task: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是学校治理与教育数据汇报 PPT 策划专家。"
                    "你只根据用户提供的本地证据和图表信息整理 PPT 大纲，不联网，不补造数据。"
                    "输出必须是严格 JSON 对象，不能包含 markdown、解释或代码块。"
                ),
            },
            {
                "role": "user",
                "content": _llm_slide_prompt(title=title, slides=slides, charts=charts, evidence_by_task=evidence_by_task),
            },
        ],
        "temperature": float(config["temperature"]),
        "max_tokens": int(config["max_tokens"]),
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        config["base_url"].rstrip("/") + "/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config['api_key']}"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=float(config["timeout_sec"])) as response:  # nosec - configured OpenAI-compatible model endpoint.
        body = response.read()
    raw = json.loads(body.decode("utf-8"))
    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("llm slide response is not a JSON object")
    return parsed


def _llm_slide_prompt(*, title: str, slides: List[Dict[str, Any]], charts: List[Dict[str, Any]], evidence_by_task: Dict[str, Any]) -> str:
    lines = [
        f"主题：{title}",
        "",
        "请把以下页面草稿、图表和证据整理成一份中文学校管理汇报 PPT 大纲。",
        "风格要求：专业、结论先行、面向校长/中层管理者；页面标题要能表达判断，不要只写“数据概览”。",
        "事实边界：只能使用给定证据；如果证据不足，用“需补充核实”表达，不要编造数字、政策或链接。",
        "输出 JSON schema：",
        '{"title":"PPT标题","slides":[{"title":"页面标题","bullets":["3到6条短句"],"speaker_notes":"可选备注"}]}',
        "",
        "页面草稿：",
    ]
    for slide in slides:
        lines.append(f"## {slide.get('title') or '页面'}")
        for bullet in slide.get("bullets") or []:
            lines.append(f"- {bullet}")
    if charts:
        lines.append("\n图表摘要：")
        for chart in charts[:4]:
            lines.append(f"- {chart.get('title') or '图表'}: {chart.get('data') or ''}")
    if evidence_by_task:
        lines.append("\n证据任务：")
        for task_id, task in list(evidence_by_task.items())[:6]:
            if isinstance(task, dict):
                compact = {
                    "purpose": task.get("purpose") or task.get("sub_question") or "",
                    "row_count": task.get("row_count"),
                    "columns": task.get("columns"),
                    "rows": task.get("rows") or task.get("sample_rows"),
                }
                lines.append(f"- {task_id}: {json.dumps(json_safe(compact), ensure_ascii=False)[:1200]}")
    return "\n".join(lines)[:12000]


def _parse_json_object(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
    return {}


def _sanitize_llm_slides(
    generated: Dict[str, Any],
    *,
    fallback_slides: List[Dict[str, Any]],
    fallback_title: str,
    max_slides: int = 10,
) -> List[Dict[str, Any]]:
    raw_slides = generated.get("slides")
    if not isinstance(raw_slides, list):
        raw_slides = []
    slides: List[Dict[str, Any]] = []
    limit = max(1, min(max_slides, 12))
    for item in raw_slides[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:80]
        bullets = _bullet_list(item.get("bullets") or item.get("content") or [])
        if title and bullets:
            slides.append({"title": title, "bullets": bullets[:8]})
    if slides:
        return slides
    title = str(generated.get("title") or fallback_title or "汇报材料").strip()
    return fallback_slides or [{"title": title, "bullets": ["需补充核实后生成汇报内容"]}]


def _write_deck_artifacts(
    *,
    tenant_id: str,
    title: str,
    slides: List[Dict[str, Any]],
    provider: str,
    model: str,
    evidence_by_task: Dict[str, Any],
    charts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    pptx_path = safe_artifact_path(tenant_id=tenant_id, tool_name="slide", suffix=".pptx")
    preview_path = pptx_path.with_suffix(".html")
    source_path = pptx_path.with_suffix(".json")
    deck_source = _deck_source(
        title=title,
        slides=slides,
        provider=provider,
        model=model,
        evidence_by_task=evidence_by_task,
        charts=charts,
        pptx_path=pptx_path,
        preview_path=preview_path,
        source_path=source_path,
    )
    _write_pptx(pptx_path, slides)
    preview_path.write_text(_preview_html(deck_source), encoding="utf-8")
    source_path.write_text(json.dumps(json_safe(deck_source), ensure_ascii=False, indent=2), encoding="utf-8")

    pptx_url = artifact_download_url(pptx_path)
    preview_url = artifact_download_url(preview_path)
    source_url = artifact_download_url(source_path)
    safe_title = _filename_title(title)
    common_content = {
        "title": title,
        "provider": provider,
        "model": model,
        "slides": json_safe(slides),
        "preview_url": preview_url,
        "download_url": pptx_url,
        "source_url": source_url,
        "deck_id": source_path.stem,
    }
    return [
        {
            "type": "pptx",
            "format": "pptx",
            "editable": True,
            "provider": provider,
            "model": model,
            "title": title,
            "filename": f"{safe_title}.pptx",
            "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "path": str(pptx_path),
            "download_url": pptx_url,
            "preview_url": preview_url,
            "source_url": source_url,
            "page_count": len(slides),
            "content": {"path": str(pptx_path), **common_content},
        },
        {
            "type": "slide_preview",
            "format": "html",
            "preview": True,
            "provider": provider,
            "title": title,
            "filename": f"{safe_title}.html",
            "mime_type": "text/html; charset=utf-8",
            "path": str(preview_path),
            "url": preview_url,
            "download_url": preview_url,
            "content": {"path": str(preview_path), **common_content},
        },
        {
            "type": "deck_source",
            "format": "json",
            "editable_source": True,
            "provider": provider,
            "model": model,
            "title": title,
            "filename": f"{safe_title}.json",
            "mime_type": "application/json",
            "path": str(source_path),
            "download_url": source_url,
            "content": {"path": str(source_path), **common_content},
        },
    ]


def _deck_source(
    *,
    title: str,
    slides: List[Dict[str, Any]],
    provider: str,
    model: str,
    evidence_by_task: Dict[str, Any],
    charts: List[Dict[str, Any]],
    pptx_path: Path,
    preview_path: Path,
    source_path: Path,
) -> Dict[str, Any]:
    return {
        "deck_id": source_path.stem,
        "title": title,
        "slides": json_safe(slides),
        "provider": provider,
        "model": model,
        "artifact_paths": {
            "pptx": str(pptx_path),
            "preview_html": str(preview_path),
            "deck_source": str(source_path),
        },
        "artifact_urls": {
            "pptx": artifact_download_url(pptx_path),
            "preview_html": artifact_download_url(preview_path),
            "deck_source": artifact_download_url(source_path),
        },
        "source_evidence": {
            "evidence_by_task": json_safe(evidence_by_task),
            "charts": json_safe(charts),
        },
    }


def _preview_html(deck_source: Dict[str, Any]) -> str:
    title = escape(str(deck_source.get("title") or "汇报材料"))
    slides = deck_source.get("slides") if isinstance(deck_source.get("slides"), list) else []
    slide_html = []
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            continue
        slide_title = escape(str(slide.get("title") or f"第 {index} 页"))
        bullets = slide.get("bullets") if isinstance(slide.get("bullets"), list) else []
        items = "\n".join(f"<li>{escape(str(item))}</li>" for item in bullets[:10])
        slide_html.append(
            f"""
            <section class="slide">
              <div class="page-num">{index:02d}</div>
              <h2>{slide_title}</h2>
              <ul>{items}</ul>
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#64748b; --line:#d8dee8; --bg:#f4f7fb; --accent:#2563eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 28px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--ink); }}
    header {{ max-width: 1120px; margin: 0 auto 18px; display:flex; justify-content:space-between; gap:16px; align-items:end; }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.25; }}
    .hint {{ color: var(--muted); font-size: 13px; }}
    main {{ max-width: 1120px; margin: 0 auto; display: grid; gap: 20px; }}
    .slide {{ position: relative; aspect-ratio: 16 / 9; background: #fff; border: 1px solid var(--line); box-shadow: 0 12px 36px rgba(15,23,42,.08); padding: 48px 60px; overflow: hidden; }}
    .slide::before {{ content:""; position:absolute; inset:0 0 auto 0; height:8px; background: linear-gradient(90deg, var(--accent), #16a34a, #f59e0b); }}
    .page-num {{ position:absolute; right:28px; top:24px; color:#94a3b8; font-size:14px; }}
    h2 {{ margin: 0 0 28px; font-size: 34px; line-height:1.25; max-width: 82%; }}
    ul {{ margin: 0; padding-left: 26px; display:grid; gap:14px; font-size: 22px; line-height: 1.45; }}
    li::marker {{ color: var(--accent); }}
    @media (max-width: 760px) {{
      body {{ padding: 14px; }}
      header {{ display:block; }}
      .slide {{ padding: 28px 24px; aspect-ratio: auto; min-height: 420px; }}
      h2 {{ font-size: 24px; max-width: 100%; }}
      ul {{ font-size: 17px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="hint">HTML 预览用于前端查看；可编辑版本请下载 PPTX。</div>
  </header>
  <main>
    {''.join(slide_html)}
  </main>
</body>
</html>"""


def _filename_title(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(title or "汇报材料").strip())
    cleaned = cleaned.strip("._")[:48]
    return cleaned or "slides"


def _bullet_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items()]
    return []


def _chart_bullets(data: Any) -> List[str]:
    if isinstance(data, dict):
        labels = data.get("labels")
        datasets = data.get("datasets")
        if isinstance(labels, list) and isinstance(datasets, list) and datasets:
            first = datasets[0] if isinstance(datasets[0], dict) else {}
            values = first.get("values") if isinstance(first, dict) else []
            out = []
            for label, value in zip(labels[:6], values[:6]):
                out.append(f"{label}: {value}")
            return out
    if isinstance(data, list):
        return [str(item)[:120] for item in data[:6]]
    return []


def _evidence_bullets(evidence_by_task: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for task_id, task in list(evidence_by_task.items())[:8]:
        if not isinstance(task, dict):
            continue
        row_count = task.get("row_count")
        purpose = task.get("purpose") or task.get("sub_question") or ""
        bullets.append(f"{task_id}: {purpose}，记录数 {row_count if row_count is not None else '未标注'}")
    return bullets or ["当前没有结构化数据证据"]


def _write_pptx(path: Path, slides: List[Dict[str, Any]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _content_types(len(slides)))
        package.writestr("_rels/.rels", _root_rels())
        package.writestr("ppt/presentation.xml", _presentation_xml(len(slides)))
        package.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(len(slides)))
        package.writestr("ppt/slideMasters/slideMaster1.xml", _empty_master())
        package.writestr("ppt/slideLayouts/slideLayout1.xml", _empty_layout())
        package.writestr("ppt/theme/theme1.xml", _theme_xml())
        package.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _master_rels())
        for index, slide in enumerate(slides, start=1):
            package.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(slide))


def _content_types(slide_count: int) -> str:
    slide_overrides = "\n".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
{slide_overrides}
</Types>'''


def _root_rels() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>'''


def _presentation_xml(slide_count: int) -> str:
    ids = "\n".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId{slide_count + 1}"/></p:sldMasterIdLst>
<p:sldIdLst>{ids}</p:sldIdLst>
<p:sldSz cx="12192000" cy="6858000" type="wide"/>
<p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>'''


def _presentation_rels(slide_count: int) -> str:
    rels = [
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, slide_count + 1)
    ]
    rels.append(
        f'<Relationship Id="rId{slide_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(rels)}</Relationships>'''


def _empty_master() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles/></p:sldMaster>'''


def _empty_layout() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sldLayout>'''


def _master_rels() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'''


def _theme_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Yili"><a:themeElements><a:clrScheme name="Yili"><a:dk1><a:srgbClr val="111827"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="334155"/></a:dk2><a:lt2><a:srgbClr val="F8FAFC"/></a:lt2><a:accent1><a:srgbClr val="2563EB"/></a:accent1><a:accent2><a:srgbClr val="16A34A"/></a:accent2><a:accent3><a:srgbClr val="F59E0B"/></a:accent3><a:accent4><a:srgbClr val="DC2626"/></a:accent4><a:accent5><a:srgbClr val="7C3AED"/></a:accent5><a:accent6><a:srgbClr val="0891B2"/></a:accent6><a:hlink><a:srgbClr val="2563EB"/></a:hlink><a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink></a:clrScheme><a:fontScheme name="Yili"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/></a:minorFont></a:fontScheme><a:fmtScheme name="Yili"/></a:themeElements></a:theme>'''


def _slide_xml(slide: Dict[str, Any]) -> str:
    title = escape(str(slide.get("title") or ""))
    bullets = [escape(str(item)) for item in slide.get("bullets") or []]
    bullet_runs = "\n".join(_text_paragraph(item, y=1700000 + index * 520000, font_size=2200) for index, item in enumerate(bullets[:9]))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
{_text_box(title, x=650000, y=420000, cx=10800000, cy=760000, font_size=3400, bold=True)}
{bullet_runs}
</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'''


def _text_paragraph(text: str, *, y: int, font_size: int) -> str:
    return _text_box(f"• {text}", x=850000, y=y, cx=10400000, cy=420000, font_size=font_size, bold=False)


def _text_box(text: str, *, x: int, y: int, cx: int, cy: int, font_size: int, bold: bool) -> str:
    bold_attr = ' b="1"' if bold else ""
    shape_id = abs(hash((text, x, y))) % 100000 + 10
    return f'''<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="TextBox {shape_id}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square"/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="{font_size}"{bold_attr}/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>'''


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _output(
    started: float,
    *,
    ok: bool,
    evidence: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> AgentToolOutput:
    return AgentToolOutput(
        ok=ok,
        evidence=evidence or [],
        artifacts=artifacts or [],
        warnings=warnings or [],
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
