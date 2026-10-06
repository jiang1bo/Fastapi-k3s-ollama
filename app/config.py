"""集中读取并校验由环境变量注入的服务配置。"""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """应用运行配置；Kubernetes ConfigMap 可以覆盖这些默认值。"""

    # Ollama 集群内 DNS 名称；本项目复用现有 local-ai/ollama-internal Service。
    ollama_base_url: str

    # 仅允许使用此逗号分隔白名单中的模型，避免请求任意启动其他模型。
    ollama_models: tuple[str, ...]

    # 每轮生成的最大 token 数；CPU-only 虚拟机应先使用较小值。
    max_new_tokens: int

    # Ollama 提示上下文长度；上下文增大将增加模型内存占用。
    num_ctx: int

    # 限制 FastAPI 同时发往 Ollama 的请求数量，保护 CPU 和内存。
    max_concurrent_requests: int

    # 对 Ollama API 的单次请求超时秒数，CPU 生成可能较慢。
    request_timeout_seconds: float

    # 保持已加载模型的时间；较短时间适合小内存环境和模型切换。
    model_keep_alive: str

    # 对话中最多保留多少条先前消息。
    max_history_messages: int

    @classmethod
    def from_environment(cls) -> "Settings":
        """将环境变量解析为有边界的设置，并在配置错误时明确失败。"""
        models = tuple(
            item.strip()
            for item in os.getenv("OLLAMA_MODELS", "qwen2.5:0.5b").split(",")
            if item.strip()
        )
        if not models:
            raise ValueError("OLLAMA_MODELS 至少需要配置一个模型标签。")

        settings = cls(
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL",
                "http://ollama-internal.local-ai.svc.cluster.local:11434",
            ).rstrip("/"),
            ollama_models=models,
            max_new_tokens=int(os.getenv("MAX_NEW_TOKENS", "128")),
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "2048")),
            max_concurrent_requests=int(
                os.getenv("MAX_CONCURRENT_REQUESTS", "1")
            ),
            request_timeout_seconds=float(
                os.getenv("OLLAMA_TIMEOUT_SECONDS", "600")
            ),
            model_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "5m"),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "10")),
        )

        # 拒绝无效的容量参数，避免服务启动后才因信号量或推理配置失败。
        if settings.max_new_tokens < 1:
            raise ValueError("MAX_NEW_TOKENS 必须大于 0。")
        if settings.num_ctx < 1:
            raise ValueError("OLLAMA_NUM_CTX 必须大于 0。")
        if settings.max_concurrent_requests < 1:
            raise ValueError("MAX_CONCURRENT_REQUESTS 必须大于 0。")
        if settings.request_timeout_seconds <= 0:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS 必须大于 0。")
        if settings.max_history_messages < 0:
            raise ValueError("MAX_HISTORY_MESSAGES 不能小于 0。")
        return settings
