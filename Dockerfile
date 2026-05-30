FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends bash openssl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY crypto/requirements.txt /app/crypto/requirements.txt

RUN python -m pip install --upgrade pip && \
    pip install -r /app/requirements.txt

COPY . /app

CMD ["python", "-c", "print('Enc2Health container image. Use docker compose services to run the stack.')"]