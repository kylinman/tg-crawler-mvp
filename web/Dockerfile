# 使用 AWS Public ECR 上的官方 library 镜像，避免 docker.io / auth.docker.io 超时
FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy shared code first (Phase 0 optimization for common/ module)
COPY common ./common
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy web code
COPY web ./web

EXPOSE 8080
# PYTHONPATH for common imports; compose may override command and working_dir
CMD ["sh", "-c", "PYTHONPATH=/app uvicorn --app-dir /app/web main:app --host 0.0.0.0 --port 8080"]
