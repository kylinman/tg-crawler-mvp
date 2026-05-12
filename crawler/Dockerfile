FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends     gcc     libpq-dev     tesseract-ocr     tesseract-ocr-chi-sim     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
