"""定义聊天 API 的输入输出结构，集中进行请求边界校验。"""

from typing import Literal

from pydantic import BaseModel, Field


MessageRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """一条已完成的对话消息；system prompt 由后端统一管理。"""

    # 只接收普通用户和助手历史，客户端不能伪造 system 指令。
    role: MessageRole

    # 限制单条历史大小，降低恶意或意外的大请求对 VM 的影响。
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    """提交给 /api/chat 的问题、模型标签和已有对话历史。"""

    # 用户当前问题必须非空且不能超过接口允许的长度。
    message: str = Field(min_length=1, max_length=10000)

    # 模型标签还会在运行时与 OLLAMA_MODELS 白名单求交集。
    model: str | None = Field(default=None, min_length=1, max_length=200)

    # history 上限由路由按配置裁剪，Pydantic 负责验证每项结构。
    history: list[ChatMessage] = Field(default_factory=list)


class ModelOption(BaseModel):
    """网页模型下拉菜单所需的标签和本机安装状态。"""

    # 模型标签来自服务端白名单，不从用户输入任意拼接。
    id: str
    label: str
    available: bool


class ModelListResponse(BaseModel):
    """当前可供选择的模型及其本地 Ollama 安装状态。"""

    models: list[ModelOption]


class ChatResponse(BaseModel):
    """模型回答及 Ollama 返回的 token / 推理耗时数据。"""

    answer: str
    model: str

    # 若 Ollama 版本未提供某项统计则返回 null，不伪报成 0 token。
    prompt_tokens: int | None
    generated_tokens: int | None
    prompt_duration_seconds: float | None
    generation_duration_seconds: float | None
    total_duration_seconds: float | None
    generation_tokens_per_second: float | None
