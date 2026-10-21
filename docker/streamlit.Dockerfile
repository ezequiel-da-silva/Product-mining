FROM python:3.11-slim

WORKDIR /app

# Instala dependências necessárias
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências
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

# Expõe a porta analítica
EXPOSE 8501

# Comando para rodar o dashboard (assumindo que você crie um arquivo src/dashboard.py no futuro)
CMD ["streamlit", "run", "src/dashboard.py", "--server.port=8501", "--server.address=0.0.0.0"]
