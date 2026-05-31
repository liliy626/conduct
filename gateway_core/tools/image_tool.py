from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .artifact_store import artifact_download_url, safe_artifact_path
from .privacy import contains_sensitive_context, sanitize_visual_prompt
from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


DashScopeImageCall = Callable[..., Any]
OpenAIImageCall = Callable[..., Any]
SUPPORTED_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024", "1792x1024", "1024x1792"}

BI_IMAGE_STYLE_PROMPT = """
【默认视觉风格】
生成一张现代数据可视化页面，整体参考苹果极简设计、飞书企业应用、浅色办公软件看板。
采用浅色高级数据产品风格：白色与浅灰背景、大量留白、圆角卡片、轻阴影、企业级运营分析平台质感。
配色使用低饱和高级色：清爽蓝、薄荷绿、柔和橙、浅紫、珊瑚红、天蓝色；背景使用极浅灰、纯白、雾蓝灰；文字使用深蓝黑和蓝灰色。
不要传统深蓝科技大屏，不要抬头显示器风格，不要赛博朋克，不要霓虹发光，不要黑色背景，不要复杂装饰纹理，不要高饱和荧光色。

【图表与版式】
画面比例优先横向宽屏，也可以标准横向画幅；像真实智慧校园数据系统中的专业分析模块。
顶部要有清晰中文标题和统计周期；关键指标用三到四个指标卡片呈现。
主视觉优先使用组合趋势图、堆叠柱状图、折线图、面积图、排行条形图、环形图等数据产品常见图表。
图例使用小色块 + 中文名称；坐标轴文字清晰；网格线用浅灰虚线；关键峰值加数字标签；关键事件用克制气泡标签。
底部可放汇总卡片、类别占比、数据来源说明和简短洞察。
中文文字必须清晰可读，信息层级明确，不要拥挤，不要生成乱码。

【数据约束】
数字绝对不能乱生成。所有数字、日期、百分比、排名、峰值、环比、同比、合计、均值、坐标轴标签和业务结论，必须严格来自用户提示中明确提供的数据。
不要自行推算、补全、改写或美化任何未提供的数字；不要把示例数字、占位数字或常识数字画进图里。
如果用户提示没有提供某个指标、环比、百分比、最高峰值、统计周期或分类合计，就不要显示该项；可以改成“未提供数据”或直接省略该卡片或标签。
如果用户提示只提供了少量数据，只画这些已提供数据对应的图形；不要为了填满三十一天、完整坐标轴或底部汇总而虚构日期和数值。
图表中的横轴日期、纵轴刻度、折线点、柱状图高度、气泡标签、底部汇总数字都必须与用户提示中的数据一致。
如果无法保证中文数字完全准确，优先减少图中文字和数字，只保留标题、真实统计周期、已提供的关键数字和简洁图例。
必须优先使用用户提示中提供的真实数据、日期、指标和分类；不要编造与用户数据冲突的数字。
如果用户提示中没有具体数字，可以做概念型数据视觉，但必须明确是“示意图”，不要假装为真实统计结果。
不要生成真实人物肖像、学生或教师个人隐私明细或虚构学校标志。
""".strip()


class ImageTool(AgentTool):
    name = "image"
    description = "Generates an AI image or visual asset through DashScope Qwen Image."

    def __init__(
        self,
        *,
        enabled: bool = False,
        dashscope_call: Optional[DashScopeImageCall] = None,
        openai_call: Optional[OpenAIImageCall] = None,
    ) -> None:
        self.enabled = enabled
        self.dashscope_call = dashscope_call
        self.openai_call = openai_call

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        blocked, reason = contains_sensitive_context(
            {key: value for key, value in tool_input.arguments.items() if key not in {"prompt", "style"}}
        )
        warnings: List[str] = []
        if blocked:
            warnings.append(f"privacy context discarded before image generation: {reason}")

        prompt = sanitize_visual_prompt(str(tool_input.arguments.get("prompt") or ""))
        style = sanitize_visual_prompt(str(tool_input.arguments.get("style") or ""))
        if not prompt:
            return _output(started, ok=False, error="image tool requires prompt")

        size = str(tool_input.arguments.get("size") or "1024x1024")
        if size not in SUPPORTED_IMAGE_SIZES:
            return _output(
                started,
                ok=False,
                error=f"unsupported image size: {size}",
                warnings=[f"supported image sizes: {', '.join(sorted(SUPPORTED_IMAGE_SIZES))}"],
            )

        if not self.enabled:
            return _output(started, ok=True, warnings=["image tool disabled; returning without external generation"])

        provider_name = _image_provider()
        image_refs = _image_refs(tool_input.arguments)
        mask_ref = _mask_ref(tool_input.arguments)
        is_edit = bool(image_refs)
        if is_edit and provider_name != "openai":
            return _output(started, ok=False, error="image editing requires GATEWAY_IMAGE_PROVIDER=openai")
        image_prompt = (
            build_image_edit_prompt(_combined_prompt(prompt=prompt, style=style))
            if is_edit
            else build_image_generation_prompt(_combined_prompt(prompt=prompt, style=style))
        )
        try:
            provider_result = _generate_provider_image(
                image_prompt,
                size=size,
                provider_name=provider_name,
                image_refs=image_refs,
                mask_ref=mask_ref,
                dashscope_call=self.dashscope_call,
                openai_call=self.openai_call,
            )
        except Exception as exc:
            return _output(started, ok=False, error=str(exc))

        artifacts, artifact_warnings = _image_artifacts(provider_result, context=context, provider_name=provider_name)
        warnings.extend(artifact_warnings)
        if not artifacts:
            return _output(
                started,
                ok=False,
                error=f"{provider_name} image call returned no usable image artifact",
                warnings=warnings,
            )

        return _output(
            started,
            ok=True,
            evidence=[
                {
                    "kind": "image_edit_prompt" if is_edit else "image_prompt",
                    "provider": provider_name,
                    "model": _image_model(provider_name),
                    "size": size,
                    "mode": "edit" if is_edit else "generate",
                    "input_image_count": len(image_refs),
                    "mask_provided": bool(mask_ref),
                }
            ],
            artifacts=artifacts,
            warnings=warnings,
        )


class GenerateImageTool(ImageTool):
    """Compatibility alias matching yili-ai-backend's LangChain tool name."""

    name = "generate_image_tool"
    description = "当用户要求“生成图片”、“画图”或“视觉化展示”时调用此工具，返回图片 URL。"


def generate_image_tool_text(
    prompt: str,
    *,
    dashscope_call: Optional[DashScopeImageCall] = None,
    openai_call: Optional[OpenAIImageCall] = None,
) -> str:
    """LangChain-native image tool body, matching yili-ai-backend's plain text contract."""
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return "\n❌ 生成失败: prompt is required"
    try:
        provider_name = _image_provider()
        provider_result = _generate_provider_image(
            build_image_generation_prompt(clean_prompt),
            size=_image_size(),
            provider_name=provider_name,
            dashscope_call=dashscope_call,
            openai_call=openai_call,
        )
    except Exception as exc:
        return f"\n❌ 生成失败: {exc}"
    for item in _provider_data_items(provider_result):
        url = str(item.get("url") or item.get("image_url") or item.get("output_url") or "").strip()
        if url:
            return f"\n✅ 生成成功！图片链接: {url}"
    return "\n❌ 生成失败: no image URL returned"


def _generate_provider_image(
    prompt: str,
    *,
    size: str,
    provider_name: str,
    image_refs: Optional[List[str]] = None,
    mask_ref: str = "",
    dashscope_call: Optional[DashScopeImageCall] = None,
    openai_call: Optional[OpenAIImageCall] = None,
) -> Dict[str, Any]:
    if provider_name == "openai":
        if image_refs:
            return _openai_edit_image(prompt, size=size, image_refs=image_refs, mask_ref=mask_ref, openai_call=openai_call)
        return _openai_generate_image(prompt, size=size, openai_call=openai_call)
    return _dashscope_generate_image(prompt, dashscope_call=dashscope_call)


def build_image_generation_prompt(prompt: str) -> str:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return BI_IMAGE_STYLE_PROMPT
    scene_hint = _image_scene_hint(clean_prompt)
    parts = [clean_prompt, BI_IMAGE_STYLE_PROMPT]
    if scene_hint:
        parts.append(scene_hint)
    return "\n\n".join(parts)


def build_image_edit_prompt(prompt: str) -> str:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return "请根据输入图片进行编辑，保持主体自然、清晰、专业。"
    return "\n\n".join(
        [
            clean_prompt,
            "【图片编辑约束】保留输入图片中未被要求修改的主体、构图、文字和关键视觉元素；只执行用户明确要求的修改。",
            "如果是在编辑数据图表或校园汇报图，严禁新增、改写或编造未在用户提示中提供的数字、日期、比例、排名和标签。",
        ]
    )


def _image_scene_hint(prompt: str) -> str:
    text = str(prompt or "")
    if any(token in text for token in ("行规", "德育", "扣分", "纪律", "两操", "卫生检查")):
        return (
            "【校园德育/行规场景补充】\n"
            "主题应体现校园日常行为规范分析，重点突出趋势、分类分布、班级/年级对比、异常峰值和整改建议。"
        )
    if any(token in text for token in ("请假", "晨午检", "健康", "病假", "缺勤", "返校")):
        return (
            "【出勤健康场景补充】\n"
            "主题应体现出勤趋势、原因占比、风险提醒和时间变化，配色可增加柔和橙色作为预警辅助色。"
        )
    if any(token in text for token in ("教师发展", "积分", "成果", "职称", "荣誉", "述职")):
        return (
            "【教师发展场景补充】\n"
            "主题应体现专业成长分析，重点突出积分结构、成果层级、排名对比和发展建议，整体更正式、稳重。"
        )
    if any(token in text for token in ("驾驶舱", "业务域", "概况", "关键指标", "运营")):
        return (
            "【校级驾驶舱场景补充】\n"
            "主题应体现多业务域运营总览，使用多指标卡片、小型趋势图和状态标签，但保持浅色办公软件看板风格，而不是传统深色大屏。"
        )
    return ""


def _combined_prompt(*, prompt: str, style: str) -> str:
    if not style:
        return prompt
    return f"{prompt}\n\n视觉风格：{style}"


def _dashscope_generate_image(prompt: str, *, dashscope_call: Optional[DashScopeImageCall] = None) -> Dict[str, Any]:
    """Use the same DashScope MultiModalConversation pattern as yili-ai-backend."""
    api_key = os.getenv(_dashscope_api_key_env(), "").strip()
    if not api_key:
        raise RuntimeError(f"missing env var: {_dashscope_api_key_env()}")

    call = dashscope_call
    dashscope_module = None
    if call is None:
        try:
            import dashscope  # type: ignore
            from dashscope import MultiModalConversation  # type: ignore
        except Exception as exc:
            raise RuntimeError("dashscope package is required for image generation") from exc
        dashscope_module = dashscope
        call = MultiModalConversation.call

    if dashscope_module is not None:
        dashscope_module.base_http_api_url = _dashscope_base_http_api_url()
        dashscope_module.api_key = api_key

    response = call(
        model=_dashscope_image_model(),
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
    )
    status_code = int(getattr(response, "status_code", 0) or _mapping_get(response, "status_code", 0) or 0)
    if status_code and status_code != 200:
        code = getattr(response, "code", "") or _mapping_get(response, "code", "")
        message = getattr(response, "message", "") or _mapping_get(response, "message", "")
        raise RuntimeError(f"dashscope image generation failed: {code} - {message}".strip())

    raw_content = _dashscope_message_content(response)
    images = []
    for item in raw_content:
        if isinstance(item, dict) and item.get("image"):
            images.append({"url": str(item["image"])})
        elif isinstance(item, dict) and item.get("url"):
            images.append({"url": str(item["url"])})
    return {
        "provider": "dashscope",
        "model": _dashscope_image_model(),
        "data": images,
        "raw": json_safe(response),
    }


def _openai_generate_image(prompt: str, *, size: str, openai_call: Optional[OpenAIImageCall] = None) -> Dict[str, Any]:
    api_key_env = _openai_api_key_env()
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"missing env var: {api_key_env}")

    call = openai_call
    if call is None:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:
            raise RuntimeError("openai package is required for OpenAI image generation") from exc
        kwargs: Dict[str, Any] = {"api_key": api_key}
        base_url = _openai_base_url()
        if base_url:
            kwargs["base_url"] = base_url
        timeout = _openai_timeout_sec()
        if timeout:
            kwargs["timeout"] = timeout
        client = OpenAI(**kwargs)
        call = client.images.generate

    response = call(
        model=_openai_image_model(),
        prompt=prompt,
        size=_openai_size(size),
    )
    images: List[Dict[str, str]] = []
    for item in _openai_data_items(response):
        url = str(item.get("url") or "").strip()
        b64 = str(item.get("b64_json") or item.get("base64") or "").strip()
        if url:
            images.append({"url": url})
        elif b64:
            images.append({"b64_json": b64})
    return {
        "provider": "openai",
        "model": _openai_image_model(),
        "mode": "generate",
        "data": images,
        "raw": json_safe(response),
    }


def _openai_edit_image(
    prompt: str,
    *,
    size: str,
    image_refs: List[str],
    mask_ref: str = "",
    openai_call: Optional[OpenAIImageCall] = None,
) -> Dict[str, Any]:
    api_key_env = _openai_api_key_env()
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"missing env var: {api_key_env}")

    call = openai_call
    if call is None:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:
            raise RuntimeError("openai package is required for OpenAI image editing") from exc
        kwargs: Dict[str, Any] = {"api_key": api_key}
        base_url = _openai_base_url()
        if base_url:
            kwargs["base_url"] = base_url
        timeout = _openai_timeout_sec()
        if timeout:
            kwargs["timeout"] = timeout
        client = OpenAI(**kwargs)
        call = client.images.edit

    files: List[Any] = []
    temp_paths: List[Path] = []
    mask_file: Any = None
    try:
        files, temp_paths = _open_image_ref_files(image_refs[:16])
        if not files:
            raise RuntimeError("image editing requires at least one valid image_url, image_path, or images item")
        extra_temp_paths: List[Path] = []
        if mask_ref:
            mask_files, extra_temp_paths = _open_image_ref_files([mask_ref])
            if mask_files:
                mask_file = mask_files[0]
                temp_paths.extend(extra_temp_paths)
        response = call(
            model=_openai_image_model(),
            image=files if len(files) > 1 else files[0],
            prompt=prompt,
            size=_openai_size(size),
            **({"mask": mask_file} if mask_file is not None else {}),
        )
    finally:
        for handle in files:
            try:
                handle.close()
            except Exception:
                pass
        if mask_file is not None:
            try:
                mask_file.close()
            except Exception:
                pass
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    images: List[Dict[str, str]] = []
    for item in _openai_data_items(response):
        url = str(item.get("url") or "").strip()
        b64 = str(item.get("b64_json") or item.get("base64") or "").strip()
        if url:
            images.append({"url": url})
        elif b64:
            images.append({"b64_json": b64})
    return {
        "provider": "openai",
        "model": _openai_image_model(),
        "mode": "edit",
        "input_image_count": len(image_refs[:16]),
        "data": images,
        "raw": json_safe(response),
    }


def _openai_data_items(response: Any) -> List[Dict[str, Any]]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not isinstance(data, list):
        return []
    items: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            items.append(item)
        else:
            items.append(
                {
                    "url": getattr(item, "url", ""),
                    "b64_json": getattr(item, "b64_json", ""),
                }
            )
    return items


def _dashscope_message_content(response: Any) -> List[Any]:
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    choices = getattr(output, "choices", None)
    if choices is None and isinstance(output, dict):
        choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content if isinstance(content, list) else []


def _mapping_get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else default


def _dashscope_api_key_env() -> str:
    return (
        os.getenv("DASHSCOPE_API_KEY_ENV", "").strip()
        or os.getenv("GATEWAY_IMAGE_QWEN_API_KEY_ENV", "").strip()
        or "DASHSCOPE_API_KEY"
    )


def _dashscope_image_model() -> str:
    return (
        os.getenv("DASHSCOPE_IMAGE_MODEL", "").strip()
        or os.getenv("GATEWAY_IMAGE_QWEN_MODEL", "").strip()
        or "qwen-image-2.0"
    )


def _dashscope_base_http_api_url() -> str:
    return (
        os.getenv("DASHSCOPE_BASE_HTTP_API_URL", "").strip()
        or os.getenv("GATEWAY_IMAGE_QWEN_BASE_HTTP_API_URL", "").strip()
        or "https://dashscope.aliyuncs.com/api/v1"
    )


def _image_provider() -> str:
    provider = (
        os.getenv("GATEWAY_IMAGE_PROVIDER", "").strip()
        or os.getenv("GATEWAY_IMAGE_TOOL_PROVIDER", "").strip()
        or "dashscope"
    ).lower()
    if provider in {"openai", "gpt-image", "gpt_image"}:
        return "openai"
    return "dashscope"


def _image_model(provider_name: str) -> str:
    return _openai_image_model() if provider_name == "openai" else _dashscope_image_model()


def _image_size() -> str:
    raw = str(os.getenv("GATEWAY_IMAGE_SIZE", "1024x1024") or "1024x1024").strip()
    return raw if raw in SUPPORTED_IMAGE_SIZES else "1024x1024"


def _openai_api_key_env() -> str:
    return os.getenv("GATEWAY_IMAGE_OPENAI_API_KEY_ENV", "").strip() or "OPENAI_API_KEY"


def _openai_image_model() -> str:
    return (
        os.getenv("GATEWAY_IMAGE_OPENAI_MODEL", "").strip()
        or os.getenv("OPENAI_IMAGE_MODEL", "").strip()
        or "gpt-image-2"
    )


def _openai_base_url() -> str:
    return os.getenv("GATEWAY_IMAGE_OPENAI_BASE_URL", "").strip() or os.getenv("OPENAI_BASE_URL", "").strip()


def _openai_timeout_sec() -> float:
    try:
        return max(5.0, min(float(os.getenv("GATEWAY_IMAGE_OPENAI_TIMEOUT_SEC", "120") or "120"), 300.0))
    except Exception:
        return 120.0


def _openai_size(size: str) -> str:
    # GPT Image supports square and common portrait/landscape sizes. Older DALL-E
    # models may reject non-square sizes, so operators can still override with env.
    return size if size in SUPPORTED_IMAGE_SIZES else "1024x1024"


def _image_refs(arguments: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for key in ("image_url", "image_path", "image"):
        refs.extend(_coerce_image_ref_items(arguments.get(key)))
    for key in ("image_urls", "image_paths", "images"):
        refs.extend(_coerce_image_ref_items(arguments.get(key)))
    seen: set[str] = set()
    unique: List[str] = []
    for ref in refs:
        clean = str(ref or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def _mask_ref(arguments: Dict[str, Any]) -> str:
    refs: List[str] = []
    for key in ("mask_url", "mask_path", "mask"):
        refs.extend(_coerce_image_ref_items(arguments.get(key)))
    return refs[0] if refs else ""


def _coerce_image_ref_items(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return _coerce_image_ref_items(parsed)
            except Exception:
                return [text]
        return [text]
    if isinstance(value, dict):
        for key in ("url", "image_url", "path", "image_path", "download_url"):
            if value.get(key):
                return [str(value[key])]
        return []
    if isinstance(value, (list, tuple)):
        refs: List[str] = []
        for item in value:
            refs.extend(_coerce_image_ref_items(item))
        return refs
    return []


def _open_image_ref_files(refs: List[str]) -> tuple[List[Any], List[Path]]:
    handles: List[Any] = []
    temp_paths: List[Path] = []
    try:
        for ref in refs:
            path, is_temp = _image_ref_to_local_path(ref)
            if path is None:
                continue
            handles.append(path.open("rb"))
            if is_temp:
                temp_paths.append(path)
        return handles, temp_paths
    except Exception:
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def _image_ref_to_local_path(ref: str) -> tuple[Optional[Path], bool]:
    clean = str(ref or "").strip()
    if not clean:
        return None, False
    if clean.startswith("data:image/"):
        return _data_url_to_temp_image(clean), True
    if clean.startswith(("http://", "https://")):
        return _download_ref_to_temp_image(clean), True
    path = Path(clean).expanduser()
    return (path, False) if path.exists() and path.is_file() else (None, False)


def _data_url_to_temp_image(data_url: str) -> Optional[Path]:
    try:
        header, raw = data_url.split(",", 1)
        ext = ".jpg" if "jpeg" in header or "jpg" in header else ".webp" if "webp" in header else ".png"
        payload = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    return _write_temp_image(payload, suffix=ext)


def _download_ref_to_temp_image(url: str) -> Optional[Path]:
    try:
        req = urllib_request.Request(url, headers={"User-Agent": "yili-gateway-image-edit/1.0"}, method="GET")
        with urllib_request.urlopen(req, timeout=_download_timeout_sec()) as resp:  # nosec - user/tool provided image URL.
            payload = resp.read()
            content_type = str(resp.headers.get("Content-Type") or "").lower()
    except (urllib_error.URLError, TimeoutError, OSError):
        return None
    if not payload:
        return None
    suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".webp" if "webp" in content_type else ".png"
    return _write_temp_image(payload, suffix=suffix)


def _write_temp_image(payload: bytes, *, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="yili_image_edit_", suffix=suffix, delete=False)
    try:
        handle.write(payload)
        return Path(handle.name)
    finally:
        handle.close()


def _image_artifacts(provider_result: Any, *, context: ToolExecutionContext, provider_name: str) -> tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    data_items = _provider_data_items(provider_result)
    artifacts: List[Dict[str, Any]] = []
    for index, item in enumerate(data_items):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("image_url") or item.get("output_url") or "").strip()
        b64 = str(item.get("b64_json") or item.get("base64") or item.get("image_base64") or "").strip()
        if url:
            saved = _save_remote_image(url, tenant_id=context.tenant_id)
            download_url = artifact_download_url(saved) if saved is not None else url
            artifacts.append(
                {
                    "type": "image",
                    "format": "png" if saved is not None else "url",
                    "provider": provider_name,
                    "url": url,
                    "image_url": url,
                    "path": str(saved) if saved is not None else "",
                    "download_url": download_url,
                    "markdown": f"![生成的图片]({url})",
                    "content": {
                        "url": url,
                        "image_url": url,
                        "path": str(saved) if saved is not None else "",
                        "download_url": download_url,
                        "markdown": f"![生成的图片]({url})",
                        "provider_result": json_safe(provider_result),
                    },
                }
            )
            continue
        if b64:
            saved = _save_base64_image(b64, tenant_id=context.tenant_id)
            if saved is None:
                warnings.append(f"image {index + 1} could not be decoded")
                continue
            artifacts.append(
                {
                    "type": "image",
                    "format": "png",
                    "provider": provider_name,
                    "path": str(saved),
                    "download_url": artifact_download_url(saved),
                    "markdown": f"![生成的图片]({artifact_download_url(saved)})",
                    "content": {
                        "path": str(saved),
                        "download_url": artifact_download_url(saved),
                        "markdown": f"![生成的图片]({artifact_download_url(saved)})",
                    },
                }
            )
    return artifacts, warnings


def _provider_data_items(provider_result: Any) -> List[Dict[str, Any]]:
    if isinstance(provider_result, dict):
        if isinstance(provider_result.get("data"), list):
            return [item for item in provider_result["data"] if isinstance(item, dict)]
        output = provider_result.get("output")
        if isinstance(output, dict):
            if isinstance(output.get("results"), list):
                return [item for item in output["results"] if isinstance(item, dict)]
            if isinstance(output.get("choices"), list):
                return [item for item in output["choices"] if isinstance(item, dict)]
        if any(key in provider_result for key in ("url", "image_url", "output_url", "b64_json", "base64", "image_base64")):
            return [provider_result]
    return []


def _save_base64_image(raw: str, *, tenant_id: str) -> Optional[Path]:
    try:
        if "," in raw and raw.split(",", 1)[0].startswith("data:"):
            raw = raw.split(",", 1)[1]
        payload = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    path = safe_artifact_path(tenant_id=tenant_id, tool_name="image", suffix=".png")
    path.write_bytes(payload)
    return path


def _save_remote_image(url: str, *, tenant_id: str) -> Optional[Path]:
    try:
        req = urllib_request.Request(url, headers={"User-Agent": "yili-gateway-image-artifact/1.0"}, method="GET")
        with urllib_request.urlopen(req, timeout=_download_timeout_sec()) as resp:  # nosec - URL is returned by configured image provider.
            payload = resp.read()
            content_type = str(resp.headers.get("Content-Type") or "").lower()
    except (urllib_error.URLError, TimeoutError, OSError):
        return None
    if not payload:
        return None
    suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
    path = safe_artifact_path(tenant_id=tenant_id, tool_name="image", suffix=suffix)
    path.write_bytes(payload)
    return path


def _download_timeout_sec() -> float:
    try:
        return max(3.0, min(float(os.getenv("GATEWAY_IMAGE_DOWNLOAD_TIMEOUT_SEC", "30") or "30"), 120.0))
    except Exception:
        return 30.0


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
