from __future__ import annotations

"""OpenWebUI 兼容网关入口（FastAPI）。

说明：
- 本文件只保留 HTTP 路由与参数透传；
- 业务路由、意图识别、数据查询与回答生成都在 gateway_core 内完成。
"""

from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse

from gateway_core.conversation.manager import ChatCompletionRequest
from gateway_core.runtime.admin.endpoints import (
    route_export_daily_merged_monitor,
    route_health,
    route_list_models,
    route_school_trace_detail,
    route_school_trace_dashboard_html,
    route_school_trace_recent,
    route_recent_question_monitor,
    route_reload_config,
    route_token_usage,
    route_token_usage_dashboard_html,
)
from gateway_core.api.openai_compat.chat_pipeline import run_chat_completions
from gateway_core.agents.jobs.endpoints import router as agent_jobs_router
from gateway_core.tools.artifact_endpoints import router as artifact_router
from gateway_core.tools.time_endpoints import router as time_tools_router


# 网关应用对象：保持轻量，不在入口层堆业务逻辑。
app = FastAPI(title="LangChain OpenWebUI Gateway", version="0.1.0")
app.include_router(agent_jobs_router)
app.include_router(artifact_router)
app.include_router(time_tools_router)


@app.get("/health")
def health() -> Dict[str, Any]:
    """基础健康检查（对内/对外都可用）。"""
    return route_health()


@app.get("/v1/admin/health")
def v1_admin_health() -> Dict[str, Any]:
    """管理端健康检查（与 /health 语义一致）。"""
    return route_health()


@app.post("/admin/reload")
def reload_config(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """热重载模型与网关配置。"""
    return route_reload_config(authorization)


@app.get("/admin/question-monitor/recent")
def recent_question_monitor(
    authorization: Optional[str] = Header(default=None),
    limit: int = 50,
) -> Dict[str, Any]:
    """读取最近的问答监控记录。"""
    return route_recent_question_monitor(authorization=authorization, limit=limit)


@app.post("/admin/question-monitor/export-daily-merged")
def export_question_monitor_daily_merged(
    authorization: Optional[str] = Header(default=None),
    day: str = "",
) -> Dict[str, Any]:
    """导出指定日期的合并监控文件。"""
    return route_export_daily_merged_monitor(authorization=authorization, day=day)


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """返回当前可用模型清单（OpenAI 兼容接口）。"""
    return route_list_models(authorization)


@app.get("/v1/admin/school-traces/recent")
def school_trace_recent(
    authorization: Optional[str] = Header(default=None),
    limit: int = 50,
) -> Dict[str, Any]:
    """读取最近的 school schema 数据流 trace。"""
    return route_school_trace_recent(authorization=authorization, limit=limit)


@app.get("/v1/admin/school-traces/ui", response_class=HTMLResponse)
def school_trace_dashboard() -> str:
    """School schema 数据流 trace 可视化页面。"""
    return route_school_trace_dashboard_html()


@app.get("/v1/admin/school-traces/dashboard", response_class=HTMLResponse)
def school_trace_dashboard_alias() -> str:
    """School schema 数据流 trace 可视化页面。"""
    return route_school_trace_dashboard_html()


@app.get("/v1/admin/token-usage")
def token_usage(
    authorization: Optional[str] = Header(default=None),
    limit: int = 1000,
) -> Dict[str, Any]:
    """按用户、API key、模型和路由汇总 token 消耗。"""
    return route_token_usage(authorization=authorization, limit=limit)


@app.get("/v1/admin/token-usage/ui", response_class=HTMLResponse)
def token_usage_dashboard() -> str:
    """Token 消耗可视化页面。"""
    return route_token_usage_dashboard_html()


@app.get("/v1/admin/school-traces/{trace_id}")
def school_trace_detail(
    trace_id: str,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """读取单次 school schema 数据流 trace 明细。"""
    return route_school_trace_detail(authorization=authorization, trace_id=trace_id)


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    req: ChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
    x_school_scope: Optional[str] = Header(default=None, alias="X-School-Scope"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_user_permissions: Optional[str] = Header(default=None, alias="X-User-Permissions"),
):
    """问答主入口。

    这里只做请求参数接收与透传，真正的处理链路在 run_chat_completions：
    - 入口 gate
    - 意图归一化
    - 学校 schema数据与政策证据规划
    - 回答生成与质量守卫
    """
    return await run_chat_completions(
        request=request,
        req=req,
        authorization=authorization,
        x_school_scope=x_school_scope,
        x_user_id=x_user_id,
        x_user_role=x_user_role,
        x_user_permissions=x_user_permissions,
    )
