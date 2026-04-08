# GPU-aware base so nvidia-smi is available in-container
FROM nvidia/cuda:12.6.3-base-ubuntu22.04

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip curl && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .
COPY index.html .

EXPOSE 9091

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:9091/api/health || exit 1

CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9091", "--log-level", "info"]
