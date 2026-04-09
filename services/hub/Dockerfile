# GPU-aware base so nvidia-smi is available for monitor module
FROM nvidia/cuda:12.6.3-base-ubuntu22.04

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip curl docker.io && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY routers/ routers/
COPY static/ static/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
