# k3s Ollama Chat：CPU 推理、token 指标与压测

这是一个独立的 FastAPI 实验项目，针对已确认的 Debian 13 单节点 k3s 环境编写。
集群已经有 `local-ai/ollama` Deployment、`ollama-internal` ClusterIP Service、
`ollama-models` PVC 和 Qwen2.5 0.5B 模型；此项目**只部署 FastAPI，不重建或改动
Ollama**。

## 已确认的目标环境

- Debian GNU/Linux 13，内核 `6.12.111+deb13-amd64`。
- 单个 Ready 节点 `debian13`，k3s `v1.36.5+k3s1`，containerd `2.3.4-k3s1.36`。
- VM 分配 4 vCPU、约 7.7 GiB RAM；没有 NVIDIA GPU。
- Ollama 位于 `local-ai/ollama` Pod，通过
  `http://ollama-internal.local-ai.svc.cluster.local:11434` 访问。
- 现有 Ollama PVC `ollama-models` 为 10 GiB `local-path` 且状态 Bound。
- 当前安装模型 `qwen2.5:0.5b`，量化 `Q4_K_M`；Ollama 日志确认 CPU 推理。
- Prometheus Operator 在 `monitoring` namespace 运行；它只选择带
  `release: prometheus` 标签的 ServiceMonitor。

## 项目功能

- FastAPI 提供聊天 API、模型列表、健康探针和静态网页。
- 只调用 `OLLAMA_MODELS` 白名单中且 Ollama 已安装的模型。
- `/api/chat` 返回 `prompt_tokens`、`generated_tokens`、模型耗时和 token/s。
- `/metrics` 导出 API 请求、延迟、模型调用数、输入/输出 token 和生成速度。
- `deploy/k3s.yaml` 创建 FastAPI Deployment、NodePort 和 Prometheus ServiceMonitor。
- Locust 脚本用于逐步压测；测试脚本用 MockTransport，不依赖真实模型下载。

## 1. 本机测试001

需要 Python 3.11 或更新版本：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

本机开发时先端口转发集群里的 Ollama：

```bash
kubectl -n local-ai port-forward svc/ollama-internal 11434:11434
```

另开终端启动服务：

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODELS=qwen2.5:0.5b
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 `http://127.0.0.1:8000/`。探针和 API：

```bash
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/api/models
curl -fsS http://127.0.0.1:8000/metrics
```

本机测试需要本机 Python 环境能访问该 Kubernetes NodePort-forward 终端；
运行在 k3s Pod 中时会直接使用集群 Service DNS，不需要 SSH 隧道。

## 2. 在 Debian 构建镜像

目标 VM 当前已确认装有 k3s，但未确认装有 Docker、Podman 或 Buildah。
可以先检查并用 Debian 软件源安装一个镜像构建器，例如 Podman：

```bash
sudo apt update
sudo apt install -y podman
```

进入此项目目录构建 OCI 镜像，然后导出为归档：

```bash
podman build -t k3s-ollama-chat:0.1.0 .
podman save --format docker-archive \
  -o /tmp/k3s-ollama-chat-0.1.0.tar \
  k3s-ollama-chat:0.1.0
```

导入 k3s 自带的 containerd 镜像库。这里需要当前用户具备 sudo 权限：

```bash
sudo k3s ctr images import /tmp/k3s-ollama-chat-0.1.0.tar
sudo k3s ctr images ls | grep k3s-ollama-chat
```

也可以推送到 k3s 节点可访问的私有 Registry；若采用 Registry，需同步修改
Deployment 镜像名和 `imagePullPolicy`。

## 3. 部署 FastAPI

该清单使用已存在的 `local-ai` namespace 和 `ollama-internal` Service，
不会变更 Ollama Deployment 或 PVC：

```bash
kubectl apply -f deploy/k3s.yaml
kubectl -n local-ai rollout status deployment/ollama-chat
kubectl -n local-ai get deployment,pods,svc
kubectl -n local-ai get servicemonitor ollama-chat
```

若镜像无法拉取，确认已执行 k3s containerd import，且 Deployment 设置的
`imagePullPolicy: Never` 与本地镜像 tag 完全一致。若 ServiceMonitor CRD 错误，
先确认 Prometheus Operator CRD 和 `monitoring` namespace 的 Operator 正常：

```bash
kubectl get crd servicemonitors.monitoring.coreos.com
kubectl -n monitoring get prometheus
```

网页和 API 地址：

```text
http://192.168.8.80:30800/
http://192.168.8.80:30800/api/models
http://192.168.8.80:30800/metrics
```

## 4. 安装更多模型

在 Ollama Pod 中下载模型；模型文件会保存到现有 PVC：

```bash
kubectl -n local-ai exec deployment/ollama -- ollama pull qwen2.5:1.5b
kubectl -n local-ai exec deployment/ollama -- ollama list
```

编辑 `deploy/k3s.yaml` 的 ConfigMap，将已安装模型加入逗号分隔白名单：

```yaml
OLLAMA_MODELS: "qwen2.5:0.5b,qwen2.5:1.5b"
```

应用后确认 FastAPI Pod 使用新配置：

```bash
kubectl apply -f deploy/k3s.yaml
kubectl -n local-ai rollout status deployment/ollama-chat
curl -fsS http://192.168.8.80:30800/api/models
```

## 5. Prometheus 查询

清单中的 ServiceMonitor 标签已匹配当前 Prometheus 的
`serviceMonitorSelector: release=prometheus`。查看监控对象和抓取目标：

```bash
kubectl -n local-ai get servicemonitor ollama-chat -o yaml
kubectl -n monitoring get prometheus
```

PromQL:

```promql
# 每个模型每小时累计输入 token
sum by (model) (increase(llm_prompt_tokens_total[1h]))
```

```promql
# 每个模型每小时累计生成 token
sum by (model) (increase(llm_generated_tokens_total[1h]))
```

```promql
# 生成 token 吞吐（token/秒）
sum by (model) (rate(llm_generated_tokens_total[5m]))
```

```promql
# FastAPI 聊天 API 的 95 分位响应延迟
histogram_quantile(
  0.95,
  sum by (le) (
    rate(fastapi_http_request_duration_seconds_bucket{path="/api/chat"}[5m])
  )
)
```

token 统计由 Ollama `/api/chat` 响应的 `prompt_eval_count` 和 `eval_count` 提供；
如果 Ollama 版本没有返回这些字段，API 会返回 null 并且不把缺失值误计为零。

## 6. 压测

建议在 Windows 或另一台客户端安装 Locust，避免压测工具消耗被测 VM CPU：

```bash
python -m venv .venv-loadtest
source .venv-loadtest/bin/activate
pip install -r requirements-loadtest.txt
locust -f loadtest/locustfile.py --host http://192.168.8.80:30800
```

打开 Locust 显示的 Web UI，先从 1 个用户、每秒启动 1 个用户开始；记录基线后
逐步提高到 2、4 个并发用户。CPU-only 模型会因应用并发限制而排队，高并发时
延迟增加属预期现象。不要把短回答的请求数等同于 token 吞吐；同时查看 Locust
延迟、Prometheus token/s 指标、VM CPU/内存和 Ollama 日志。

如果使用 CLI，可用短时限的无头测试：

```bash
locust -f loadtest/locustfile.py \
  --host http://192.168.8.80:30800 \
  --headless -u 1 -r 1 --run-time 2m
```

停止测试后保存 Locust CSV 结果并记录模型、VM vCPU/内存、上下文长度和输出长度，
才能比较不同模型配置。

## 注释约定

所有 Python、JavaScript、HTML、CSS、Dockerfile 和 Kubernetes 配置文件均包含中文
文件级说明或针对配置意图的注释；注释说明接口、安全边界、资源限制和运维注意事项。
