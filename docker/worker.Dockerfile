FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema necessárias para compilar algumas bibliotecas Python (se aplicável)
RUN apt-get update && apt-get install -y gcc libpq-dev ffmpeg && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências do projeto (assumindo que irá criar um requirements.txt na raiz)
# O worker precisa de: celery, redis, psycopg-pool, psycopg[binary], google-generativeai, pydantic, requests
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do projeto
COPY . /app

# Configura o PYTHONPATH
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# O comando de execução será injetado pelo docker-compose.yml (ex: celery -A core.celery_app worker...)
