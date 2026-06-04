FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends     gcc     libpq-dev     tesseract-ocr     tesseract-ocr-chi-sim     && rm -rf /var/lib/apt/lists/*

# Copy shared code first (Phase 0 optimization)
COPY common ./common
COPY crawler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy crawler code
COPY crawler ./crawler

# Run with PYTHONPATH so `import common` works; working dir set in compose for dev
CMD ["sh", "-c", "PYTHONPATH=/app python /app/crawler/main.py"]
