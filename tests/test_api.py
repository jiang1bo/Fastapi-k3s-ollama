"""离线 API 测试：通过 HTTPX MockTransport 模拟 Ollama，不下载模型。"""

import json
import unittest

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FastAPIApiTests(unittest.TestCase):
    """验证探针、模型白名单、回答结构和 token 统计。"""

    def setUp(self):
        # 使用已知小模型名构造隔离配置，测试不依赖运行中的 Ollama Pod。
        self.settings = Settings(
            ollama_base_url="http://ollama.test",
            ollama_models=("qwen2.5:0.5b",),
            max_new_tokens=32,
            num_ctx=512,
            max_concurrent_requests=1,
            request_timeout_seconds=5,
            model_keep_alive="1m",
            max_history_messages=4,
        )

        def fake_ollama(request: httpx.Request) -> httpx.Response:
            """返回稳定的 tags/chat 响应，供 API 测试走完整 HTTP 路由。"""
            if request.url.path == "/api/tags":
                payload = {"models": [{"name": "qwen2.5:0.5b"}]}
            elif request.url.path == "/api/chat":
                payload = {
                    "message": {"role": "assistant", "content": "测试回答"},
                    "prompt_eval_count": 17,
                    "eval_count": 6,
                    "prompt_eval_duration": 500_000_000,
                    "eval_duration": 2_000_000_000,
                    "total_duration": 3_000_000_000,
                }
            else:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=payload)

        self.app = create_app(
            self.settings,
            transport=httpx.MockTransport(fake_ollama),
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        """关闭测试客户端，确保 lifespan 正确释放异步连接池。"""
        self.client_context.__exit__(None, None, None)

    def test_health_and_model_list(self):
        """就绪探针应依赖 Ollama，模型列表只展示配置白名单。"""
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        models = self.client.get("/api/models").json()["models"]
        self.assertEqual(models, [
            {
                "id": "qwen2.5:0.5b",
                "label": "qwen2.5:0.5b",
                "available": True,
            }
        ])

    def test_chat_returns_token_usage(self):
        """聊天响应应包含回答、输入/输出 token 数以及 token/s。"""
        response = self.client.post(
            "/api/chat",
            json={
                "message": "你好",
                "history": [],
                "model": "qwen2.5:0.5b",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "测试回答")
        self.assertEqual(response.json()["prompt_tokens"], 17)
        self.assertEqual(response.json()["generated_tokens"], 6)
        self.assertEqual(response.json()["generation_tokens_per_second"], 3.0)
        metrics = self.client.get("/metrics").text
        self.assertIn(
            'llm_prompt_tokens_total{model="qwen2.5:0.5b"} 17.0',
            metrics,
        )
        self.assertIn(
            'llm_generated_tokens_total{model="qwen2.5:0.5b"} 6.0',
            metrics,
        )

    def test_unconfigured_model_is_rejected(self):
        """客户端不能跳过配置白名单而触发下载或运行任意 Ollama 模型。"""
        response = self.client.post(
            "/api/chat",
            json={"message": "你好", "model": "not-allowed:latest"},
        )
        self.assertEqual(response.status_code, 422)

    def test_prometheus_endpoint(self):
        """metrics 路由应返回 Prometheus 可抓取的 exposition 文本。"""
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])


if __name__ == "__main__":
    # 允许在项目根目录直接运行 python -m unittest discover -s tests。
    unittest.main()
