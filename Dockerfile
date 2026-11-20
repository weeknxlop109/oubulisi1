# 医药知识图谱问答系统 · 方案 A
# 统一环境：CPU 版 PyTorch + 项目全部 Python 源码，数据/模型/日志全部落 volume 可复现。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 系统依赖：git（备份推送）与 curl（容器健康探针）
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖再拷源码，充分利用构建缓存
# torch 使用 CPU wheel（index-url 已限定，避免拉取 CUDA 包）
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY . .

# 默认从 .env.example 初始化运行配置（.env 不随镜像分发）
RUN if [ ! -f .env ]; then cp .env.example .env; fi

RUN python -c "import config; config.ensure_dirs()" \
    && chmod +x up.sh verify.sh 2>/dev/null || true

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["./up.sh"]
