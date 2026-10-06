# 使用精简 Python 镜像；Ollama 和模型本身运行在独立的集群工作负载中。
FROM python:3.13-slim

# 禁用 .pyc 文件并让 Python 日志直接写入容器标准输出。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 固定工作目录，便于镜像和 Kubernetes 命令保持一致。
WORKDIR /app

# 单独复制依赖清单，让源码更新时 Docker 可以复用依赖安装缓存。
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# 只复制运行 FastAPI 必须的源码和网页资源，不把 VM 中的 Ollama 模型复制到镜像。
COPY app ./app
COPY static ./static

# Web 服务使用非 root 用户运行，减少容器进程被利用后的权限范围。
RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER 10001

# Kubernetes Service 会把流量转发到此端口。
EXPOSE 8000

# 一个 Uvicorn worker 配合应用的并发限制，避免多进程同时挤占 CPU/内存。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
