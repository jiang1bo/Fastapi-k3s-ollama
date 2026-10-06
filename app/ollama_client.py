"""将 FastAPI 请求映射到集群内 Ollama HTTP API。"""

import httpx

from .config import Settings


class OllamaClient:
    """对共享 HTTPX 客户端做轻量封装，并暴露受控的 Ollama 操作。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        # AsyncClient 由 FastAPI lifespan 创建和关闭，便于复用连接。
        self._client = client
        self._settings = settings

    async def list_models(self) -> set[str]:
        """读取 Ollama 已安装模型标签；连接或协议错误向上明确报告。"""
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", [])
        if not isinstance(models, list):
            raise RuntimeError("Ollama /api/tags 响应中 models 字段不是列表。")

        return {
            item["name"]
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }

    async def chat(self, model: str, messages: list[dict]) -> dict:
        """向指定的白名单模型提交一次非流式对话并返回完整统计信息。"""
        response = await self._client.post(
            "/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": self._settings.model_keep_alive,
                "options": {
                    "num_ctx": self._settings.num_ctx,
                    "num_predict": self._settings.max_new_tokens,
                    "temperature": 0.7,
                    "top_p": 0.95,
                    "repeat_penalty": 1.05,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("message", {}).get("content"), str):
            raise RuntimeError("Ollama 返回缺少 message.content。")
        return payload
