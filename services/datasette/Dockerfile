FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    datasette==0.65.1 \
    datasette-copyable==0.3.2 \
    datasette-export-notebook==1.0

COPY metadata.yml /app/metadata.yml
COPY settings.json /app/settings.json
COPY static/ /app/static/

WORKDIR /app

EXPOSE 8001

CMD ["datasette", "serve", \
     "/workspace/openclaw.db", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--metadata", "/app/metadata.yml", \
     "--static", "assets:/app/static", \
     "--setting", "base_url", "/data/", \
     "--setting", "sql_time_limit_ms", "10000", \
     "--setting", "max_returned_rows", "1000", \
     "--setting", "default_page_size", "50", \
     "--setting", "num_sql_threads", "3", \
     "--setting", "allow_download", "off", \
     "--cors"]
