# 앱(API) 이미지.
#
#   TORCH_VARIANT=cpu   → PyTorch CPU 휠. 이미지 약 2.5GB.
#   TORCH_VARIANT=cuda  → PyPI 기본 CUDA 휠. 이미지 약 9GB, 호스트에 NVIDIA GPU +
#                         Docker GPU 런타임이 있어야 이득이 있다.
#
# 두 이미지의 기능은 같다. app/embeddings.py 와 app/rerank.py 가
# torch.cuda.is_available() 로 분기해서 알아서 CPU 로 떨어지기 때문에,
# cpu 이미지는 임베딩/재순위가 느려질 뿐 동작은 동일하다.
ARG TORCH_VARIANT=cpu

# ---------- builder ----------
FROM python:3.11-slim AS builder
ARG TORCH_VARIANT

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# torch 를 requirements.txt 보다 먼저, 원하는 인덱스에서 못박아 깐다.
# 순서를 바꾸면 sentence-transformers 가 PyPI 의 CUDA 휠(약 3GB)을 먼저 끌어와서
# cpu 빌드가 의미를 잃는다.
RUN if [ "$TORCH_VARIANT" = "cpu" ]; then \
        pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.9.0"; \
    else \
        pip install --no-cache-dir "torch>=2.9.0"; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim
WORKDIR /app

# build-essential 은 런타임 이미지에 남기지 않는다(약 300MB).
COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY app ./app

EXPOSE 8000

# 임베딩/재순위 모델은 lru_cache 로 지연 로딩이라 기동 자체는 빠르다.
# start-period 는 첫 기동의 DB 마이그레이션·시드까지만 감안한 값.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
