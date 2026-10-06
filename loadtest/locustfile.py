"""使用 Locust 模拟短问答请求，测试模型选择、延迟和错误率。"""

import os

from locust import HttpUser, between, task
from locust.exception import StopUser


class ChatLoadUser(HttpUser):
    """一个虚拟用户持续提交问题，但每次请求之间留出等待间隔。"""

    # CPU-only Ollama 应从温和速率开始，避免一开始就耗尽 VM 资源。
    wait_time = between(1, 3)

    def on_start(self):
        """验证 API 已就绪，且压测所选模型存在于服务端白名单中。"""
        response = self.client.get("/api/models", name="/api/models")
        if response.status_code != 200:
            raise StopUser("模型目录 API 不可用，停止此虚拟用户。")

        self.model = os.getenv("MODEL", "qwen2.5:0.5b")
        available = {
            model["id"]
            for model in response.json().get("models", [])
            if model.get("available")
        }
        if self.model not in available:
            raise StopUser(
                f"MODEL={self.model!r} 不可用；可用模型：{sorted(available)}"
            )

    @task
    def generate_answer(self):
        """每个请求固定生成较短回答，方便重复比较压测结果。"""
        self.client.post(
            "/api/chat",
            json={
                "model": self.model,
                "message": "请用一句话说明地球绕什么天体公转。",
                "history": [],
            },
            name="/api/chat",
        )
