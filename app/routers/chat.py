"""聊天、模型目录和 token 统计 API。"""

import asyncio
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..metrics import (
    LLM_GENERATED_TOKENS,
    LLM_GENERATION_SPEED,
    LLM_INFERENCE_DURATION,
    LLM_PROMPT_TOKENS,
    LLM_REQUESTS,
)
from ..schemas import (
    ChatRequest,
    ChatResponse,
    ModelListResponse,
    ModelOption,
)


router = APIRouter(prefix="/api", tags=["chat"])


def _duration_seconds(payload: dict, field: str) -> float | None:
    """把 Ollama 返回的纳秒耗时安全换算为秒；缺省项保留为 null。"""
    value = payload.get(field)
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return value / 1_000_000_000


@router.get("/models", response_model=ModelListResponse)
async def list_models(request: Request):
    """只展示配置白名单内的模型，并标记每个模型是否已下载到 PVC。"""
    settings = request.app.state.settings
    try:
        installed = await request.app.state.ollama.list_models()
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"无法读取 Ollama 模型列表：{exc}",
        ) from exc

    return ModelListResponse(
        models=[
            ModelOption(
                id=model,
                label=model,
                available=model in installed,
            )
            for model in settings.ollama_models
        ]
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    """调用白名单模型回答问题，并记录 Ollama 原始 token 计数。"""
    settings = request.app.state.settings

    # 未指定时使用配置列表第一项；不在 allowlist 的值绝不传给 Ollama。
    model = body.model or settings.ollama_models[0]
    if model not in settings.ollama_models:
        raise HTTPException(status_code=422, detail="请求的模型不在服务端白名单中。")

    # 先验证标签存在；网络错误转为 502，避免异常泄漏成无解释的 500。
    try:
        installed = await request.app.state.ollama.list_models()
    except (httpx.HTTPError, RuntimeError) as exc:
        LLM_REQUESTS.labels(model, "error").inc()
        raise HTTPException(
            status_code=502,
            detail=f"无法读取 Ollama 模型列表：{exc}",
        ) from exc
    if model not in installed:
        raise HTTPException(
            status_code=503,
            detail=f"模型 {model!r} 尚未下载到 Ollama。",
        )

    # 把 system prompt 放在历史最前面，并按配置截断历史，控制 CPU 推理成本。
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个有帮助的中文助手。请直接、准确地回答用户当前问题；"
                "不要输出内部思考过程。"
            ),
        }
    ]
    if settings.max_history_messages:
        messages.extend(
            item.model_dump()
            for item in body.history[-settings.max_history_messages :]
        )
    messages.append({"role": "user", "content": body.message})

    # 信号量和 Ollama NUM_PARALLEL 配合，避免在小 VM 上同时载入多份 KV cache。
    semaphore: asyncio.Semaphore = request.app.state.inference_semaphore
    started = time.perf_counter()
    try:
        async with semaphore:
            payload = await request.app.state.ollama.chat(model, messages)
    except (httpx.HTTPError, RuntimeError) as exc:
        LLM_REQUESTS.labels(model, "error").inc()
        raise HTTPException(
            status_code=502,
            detail=f"Ollama 推理失败：{exc}",
        ) from exc

    wall_duration = time.perf_counter() - started
    LLM_REQUESTS.labels(model, "success").inc()
    LLM_INFERENCE_DURATION.labels(model).observe(
        _duration_seconds(payload, "total_duration")
        or wall_duration
    )

    # Ollama 返回 token 统计时才累计；缺失统计不虚构为零 token。
    prompt_tokens = payload.get("prompt_eval_count")
    generated_tokens = payload.get("eval_count")
    if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
        LLM_PROMPT_TOKENS.labels(model).inc(prompt_tokens)
    else:
        prompt_tokens = None
    if isinstance(generated_tokens, int) and generated_tokens >= 0:
        LLM_GENERATED_TOKENS.labels(model).inc(generated_tokens)
    else:
        generated_tokens = None

    generation_duration = _duration_seconds(payload, "eval_duration")
    tokens_per_second = None
    if generated_tokens is not None and generation_duration:
        tokens_per_second = generated_tokens / generation_duration
        LLM_GENERATION_SPEED.labels(model).observe(tokens_per_second)

    return ChatResponse(
        answer=payload["message"]["content"].strip(),
        model=model,
        prompt_tokens=prompt_tokens,
        generated_tokens=generated_tokens,
        prompt_duration_seconds=_duration_seconds(payload, "prompt_eval_duration"),
        generation_duration_seconds=generation_duration,
        total_duration_seconds=_duration_seconds(payload, "total_duration"),
        generation_tokens_per_second=tokens_per_second,
    )
