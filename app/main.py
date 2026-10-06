"""FastAPI 应用入口、探针、Prometheus 指标和本地静态聊天页面。"""

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from time import perf_counter

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings
from .metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS, KNOWN_PATHS
from .ollama_client import OllamaClient
from .routers import chat


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """建立应用实例；transport 参数允许测试使用无网络的假 Ollama。"""
    app_settings = settings or Settings.from_environment()
    app = FastAPI(title="K3s Ollama Chat", version="1.0.0")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """在进程生命周期内复用到集群 Ollama Service 的 HTTP 连接池。"""
        timeout = httpx.Timeout(app_settings.request_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=app_settings.ollama_base_url,
            timeout=timeout,
            transport=transport,
        ) as client:
            # 信号量限制本 Pod 同时进行的模型推理请求数。
            application.state.ollama = OllamaClient(client, app_settings)
            application.state.settings = app_settings
            application.state.inference_semaphore = asyncio.Semaphore(
                app_settings.max_concurrent_requests
            )
            yield

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def record_http_metrics(request: Request, call_next):
        """记录延迟和状态码；未知路径合并为 unmatched 防止标签爆炸。"""
        if request.url.path == "/metrics":
            return await call_next(request)

        route = KNOWN_PATHS.get(request.url.path, "unmatched")
        started = perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            elapsed = perf_counter() - started
            HTTP_REQUESTS.labels(request.method, route, status).inc()
            HTTP_REQUEST_DURATION.labels(request.method, route).observe(elapsed)

    app.include_router(chat.router)

    @app.get("/health/live", include_in_schema=False)
    async def liveness():
        """存活探针只检查 Web 进程，避免短暂 Ollama 故障触发重启风暴。"""
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness(request: Request):
        """就绪探针验证 Ollama 可达且至少有一个配置模型已安装。"""
        try:
            installed = await request.app.state.ollama.list_models()
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"无法读取 Ollama 模型列表：{exc}",
            ) from exc

        configured = request.app.state.settings.ollama_models
        if not any(model in installed for model in configured):
            raise HTTPException(
                status_code=503,
                detail="Ollama 可连接，但没有安装 OLLAMA_MODELS 中配置的模型。",
            )
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        """输出 Prometheus 文本格式指标，供集群 Prometheus ServiceMonitor 抓取。"""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # 前端静态目录挂载最后注册，确保 API、探针和指标路由优先匹配。
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


# Uvicorn 从 app.main:app 导入此 ASGI 应用实例。
app = create_app()
