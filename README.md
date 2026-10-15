# k3s Ollama Chat：CPU 推理、token 指标与压测

这是一个独立的 FastAPI 实验项目，针对已确认的 Debian 13 单节点 k3s 环境编写。
集群已经有 `local-ai/ollama` Deployment、`ollama-internal` ClusterIP Service、
`ollama-models` PVC 和 Qwen2.5 0.5B 模型；此项目**只部署 FastAPI，不重建或改动
Ollama**。

## 已确认的目标环境

- Debian GNU/Linux 13，内核 `6.12.111+deb13-amd64`。
- 单个 Ready 节点 `debian13`，k3s `v1.36.5+k3s1`，containerd `2.3.4-k3s1.36`。
- VM 分配 4 vCPU、约 6 GiB RAM；没有直通给 Debian 的 NVIDIA GPU。宿主机为 Windows 10 Pro，配有 GTX 1650 4 GiB 显存；除非确认 GPU 已直通并由 VM 使用，否则不能将宿主机 GPU 视为 Ollama 的可用 GPU。
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

## 1. 本机测试

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

建议从 Windows 或另一台客户端运行 Locust，避免压测工具占用被测 VM 的 CPU。
`requirements-loadtest.txt` 是压测客户端的独立依赖清单；Locust 不需要安装进
FastAPI 或 Ollama 容器。

### Windows PowerShell 环境

从 Debian 复制出来的 `.venv` 通常是 Linux 虚拟环境，里面使用 `bin/`；
Windows 虚拟环境使用 `Scripts/`，两者不能直接互换。不要激活复制来的 Linux
环境，而应在项目根目录创建独立的 Windows 环境：

```powershell
py -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
python -m pip install -r .\requirements-loadtest.txt
python -m locust --version
```

创建虚拟环境只需执行一次。若 PowerShell 阻止本次激活脚本，可仅为当前终端临时
放行后再激活：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv-win\Scripts\Activate.ps1
```

也可以不激活环境，直接通过虚拟环境中的 Python 安装和运行：

```powershell
.\.venv-win\Scripts\python.exe -m pip install -r .\requirements-loadtest.txt
.\.venv-win\Scripts\python.exe -m locust --version
```

如果 `locust` 命令不识别，使用 `python -m locust` 可避免 Windows 找不到虚拟环境
脚本目录中的命令入口。PowerShell 不使用反斜杠 `\` 作为续行符；请将命令写在
一行，或使用 PowerShell 续行符反引号 `` ` ``。

### 运行基线测试并保存结果

先创建 CSV 输出目录，然后用 1 个用户运行 5 分钟：

```powershell
New-Item -ItemType Directory -Force .\results
python -m locust -f .\loadtest\locustfile.py --host http://192.168.8.80:30800 --headless -u 1 -r 1 --run-time 5m --csv .\results\qwen-ctx2048-u1
```

这里 `-u 1` 是目标并发用户数，`-r 1` 是每秒启动用户数。`--csv` 接收输出前缀，
会生成 `_stats.csv`、`_stats_history.csv`、`_failures.csv` 等文件。测试前确认
Windows 能访问 `192.168.8.80:30800`，FastAPI Pod 已 Ready，且目标模型正常响应。

先从 1 个用户记录基线，再逐步提高并发。CPU-only 模型可能排队，增加并发后延迟
上升属预期现象。不要把请求数等同于 token 吞吐；同时查看 Locust 延迟、
Prometheus token/s、VM CPU/内存和 Ollama 日志。比较配置时记录模型、VM vCPU/
内存、上下文长度和输出长度。

## 7. 从 Windows 访问 Grafana

Grafana 在 Debian k3s 集群中运行。Windows 本机可通过 SSH 隧道转发 Grafana
端口；以下命令在 Windows PowerShell 执行并保持终端打开：

```powershell
ssh -T -L 3000:127.0.0.1:3000 fermi@192.168.8.80 "KUBECONFIG=/home/fermi/.kube/config kubectl -n monitoring port-forward svc/prometheus-grafana 3000:80"
```

然后用本机浏览器访问 `http://127.0.0.1:3000`。关闭 SSH 进程会停止转发。
Grafana 的 Service 名称和端口若有变化，先在 Debian 上用
`kubectl -n monitoring get svc` 核实。

### Windows 登录后自动启动隧道

自动化前先手动确认 SSH 密钥登录可用，并接受过服务器主机密钥提示；不要把 SSH
密码明文写进脚本。将以下内容保存为
`$env:USERPROFILE\Scripts\grafana-tunnel.ps1`（先创建 `Scripts` 目录）：

```powershell
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
& $ssh -T `
  -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -L 3000:127.0.0.1:3000 `
  fermi@192.168.8.80 `
  "KUBECONFIG=/home/fermi/.kube/config kubectl -n monitoring port-forward svc/prometheus-grafana 3000:80"
```

在 Windows「任务计划程序」创建任务：将触发器设为“用户登录时”；操作程序设为
`powershell.exe`，参数使用
`-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$env:USERPROFILE\Scripts\grafana-tunnel.ps1"`
（任务计划程序中需将 `$env:USERPROFILE` 展开成实际的 `C:\Users\<用户名>` 路径）；
并可设置任务失败后每分钟重试。保存后手动运行任务，确认
`http://127.0.0.1:3000` 可打开。**此任务只自动建立 Windows SSH 隧道，不负责
启动 Debian 上的 K3s 或 Grafana**；集群服务需由 Debian/K3s 自身启动配置管理。

## 8. 进展记录（2026-10-15）

### 当前项目副本与环境边界

- Windows 项目副本：`C:\ollama-models\pyagentcode\vm-k3s-project`。
- Debian 部署目录：`/home/fermi/work/vm-k3s-project`。Windows 目录是从 Debian
  项目复制出来的工作副本；复制后的代码和 Git 状态应分别核实，不能假设两边自动同步。
- 当前检查到 Windows 副本位于 `main` 分支，HEAD 为 `6bd1720 (python 部署)`，
  关联 `origin/main`。Linux 侧历史整理记录曾显示 `main 1b51625 first commit`；
  用户提供的综合记录另写默认分支为 `master`。这些是不同时间/副本的状态，
  当前 Windows 副本并非 `master`；远端默认分支名称也需通过 GitHub 仓库设置核实。
  继续操作前应以各自目录中的 `git status` 和 `git log` 为准。
- 原目录 `C:\ollama-models\pyagentcode\Fastapichat` 已停止使用并删除；后续以
  `vm-k3s-project` 为项目副本，不要再依赖旧路径。
- Windows 宿主机为 Windows 10 Pro，Dell G3 3590、Intel i5-9300H、32 GB 内存、
  NVIDIA GTX 1650 4 GB；Debian 13 VM 配置为 4 vCPU、6 GB RAM、40 GB 磁盘。
  用户记录宿主机当时可用内存约 8.6 GB。VM 无桌面环境。宿主机有 NVIDIA GPU
  不代表 Debian VM 或 k3s Pod 已能使用它；当前历史运行记录确认 Ollama 使用 CPU，
  GPU 直通/容器可见性仍需单独验证。

### 已完成与待确认

- 已完成 Debian 13 单节点 k3s、Prometheus/Grafana 监控栈的部署；Grafana 已汉化。
- 历史检查记录曾确认 Ollama 在 `local-ai` namespace 运行、模型 PVC 为 10 GiB
  且 Bound，安装了 `qwen2.5:0.5b`（`Q4_K_M`）。Ollama Pod 的实时状态会变化；
  用户最新整理仍将镜像拉取/Pod 状态列为卡点，需在 VM 上重新检查，不能仅凭旧记录
  判断当前已恢复。
- FastAPI 项目、部署清单、Locust 脚本、指标和离线测试已建立。历史部署中 FastAPI
  曾因镜像缺少 `httpx`、`prometheus-client` 而发生 `CrashLoopBackOff`；应确认
  修复后的依赖已进入镜像，并在集群核验 Pod Ready 和 Prometheus target `UP`。
- 2026-10-15 已定位 Windows 压测环境问题：复制来的 Linux `.venv` 使用 `bin/`
  目录，不存在 Windows 所需的 `.venv\Scripts\Activate.ps1`。解决办法是另建
  `.venv-win`，安装 `requirements-loadtest.txt`，并用
  `python -m locust` 启动；`requirements.txt` 是应用运行依赖，不包含 Locust。
- 双击 Python 源文件出现“选择用哪个应用打开”窗口与 pip 安装无关；依赖安装和
  压测命令应在 PowerShell 项目目录中执行。Windows 的 PowerShell 命令不使用
  Unix shell 的 `\` 换行写法。
- 已提供 Grafana 的 SSH 本地端口转发方式（`127.0.0.1:3000`），以及通过 Windows
  任务计划程序在用户登录时自动启动转发的配置步骤。自动化是否已配置、SSH 密钥是否
  可无交互登录，尚未确认。
- 当前 `results/` 中已有 `qwen-ctx2048-u1` Locust CSV：`/api/chat` 记录 123 次
  请求、0 次失败，中位响应时间 320 ms、平均 357 ms、P95 530 ms、最大 1,971 ms，
  平均约 0.413 请求/秒；`/api/models` 记录 1 次请求、0 次失败。这说明该组结果
  文件记录了成功请求，但不能据此证明最新一次 Windows 依赖安装、持续 5 分钟压测、
  Prometheus 抓取或当前集群健康状态均已验证。

### 下一步验证顺序

1. 在 Windows 项目副本激活 `.venv-win`，运行
   `python -m pip install -r requirements-loadtest.txt` 和
   `python -m locust --version`。
2. 在 Debian 检查 `kubectl -n local-ai get pods,svc,endpoints`，确认 Ollama 与
   FastAPI Pod Ready；确认 `http://192.168.8.80:30800/health/ready` 和 `/api/models`
   能正常响应。
3. 在 Prometheus 查询 `up{namespace="local-ai", service="ollama-chat"}`；返回 1
   后再运行 Locust，并比较错误率、P95 延迟、token/s 和 VM 资源。
4. 手动验证 Grafana SSH 隧道后，再配置 Windows 任务计划程序，并测试重新登录后
   本机浏览器能否访问。

## 9. 总体目标与路线图

本项目是本地低资源环境中的 AI 运维与 Agent 学习平台。当前可运行的交付范围是
k3s 上的 Ollama/FastAPI 聊天服务、Prometheus 指标和 Locust 压测；长期目标是在
同一套基础设施上逐步形成模型部署、服务观测、压测分析、优化、发布和回滚闭环。

规划中的能力包括：

- 在 k3s/Ollama 上评估小型模型，例如 Qwen3.5-4B、MiniCPM5-2B、
  Phi-4-mini、Qwen2.5-3B-Instruct 和 Tiny-Agent-a-1.5B。实际选型须以 4 GB
  宿主显存、VM 是否能使用 GPU、量化方式及内存测试结果为准；Debian VM 当前为
  CPU 推理环境。
- 使用 Python 构建 Agent，逐步支持本地模型聊天、运维知识库问答、PDF/Word/图片
  文档解析、RAG、任务规划及工具调用（例如查询 Pod 状态和节点资源）。
- 用 Prometheus/Grafana 监测服务、请求、模型 token 与资源；通过 Locust 建立
  不同模型和并发配置的性能基线，再据此优化。
- 在应用稳定后评估向量数据库和 RAG 原型、Argo Rollouts 灰度发布及自动回滚。
  视 VM 资源情况，考虑降低 Prometheus 保留时间或限制/关闭非必需监控组件。

规划的实施顺序是：复核网络/DNS 与 Ollama 镜像可达性；确认 Ollama 和模型可用；
让 FastAPI Pod 稳定 Ready；验证 ServiceMonitor target 为 `UP`；运行 Locust 并记录
延迟、错误率、token/s 与 CPU/内存；之后再增加知识库、Agent 工具调用及发布回滚。
WARP/DNS、镜像拉取、Pod OOM、kubeconfig 权限和 SSH 文件权限等均有历史排查记录，
但环境会变化，遇到故障时应基于当次日志和集群状态诊断，不直接复用旧结论。

## 注释约定

所有 Python、JavaScript、HTML、CSS、Dockerfile 和 Kubernetes 配置文件均包含中文
文件级说明或针对配置意图的注释；注释说明接口、安全边界、资源限制和运维注意事项。
