"""声明低基数 Prometheus 指标，统计 API 和模型 token 用量。"""

from prometheus_client import Counter, Histogram


# 使用已配置模型标签作为模型维度；不会把用户输入或任意 URL 放进标签。
HTTP_REQUESTS = Counter(
    "fastapi_http_requests_total",
    "Total HTTP requests handled by the chat service.",
    ("method", "path", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "fastapi_http_request_duration_seconds",
    "HTTP request latency in seconds, including model generation time.",
    ("method", "path"),
)
LLM_REQUESTS = Counter(
    "llm_inference_requests_total",
    "Completed and failed requests sent to Ollama.",
    ("model", "status"),
)
LLM_PROMPT_TOKENS = Counter(
    "llm_prompt_tokens_total",
    "Input prompt tokens reported by Ollama.",
    ("model",),
)
LLM_GENERATED_TOKENS = Counter(
    "llm_generated_tokens_total",
    "Generated completion tokens reported by Ollama.",
    ("model",),
)
LLM_INFERENCE_DURATION = Histogram(
    "llm_inference_duration_seconds",
    "Ollama total inference duration in seconds.",
    ("model",),
)
LLM_GENERATION_SPEED = Histogram(
    "llm_generation_tokens_per_second",
    "Generated tokens per second as reported by Ollama timings.",
    ("model",),
)

# 固定路由表防止带任意路径参数的请求创建无界 Prometheus 标签。
KNOWN_PATHS = {
    "/": "/",
    "/api/chat": "/api/chat",
    "/api/models": "/api/models",
    "/health/live": "/health/live",
    "/health/ready": "/health/ready",
}
