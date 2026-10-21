FROM python:3.11-slim

WORKDIR /app

# Dependências mínimas de SO para o Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instala todas as dependências declaradas (incluindo playwright-stealth)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install-deps chromium

COPY . /app
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "src/producer.py"]
