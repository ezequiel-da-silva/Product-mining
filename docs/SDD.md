# Software Design Document - Minerador FB

## Visão Geral
Este sistema automatiza a recolha de anúncios do Facebook Ads utilizando browsers anti-detect via CDP, processando a mídia com LLMs Multimodais e armazenando embeddings vetoriais para futuras pesquisas.

## Componentes Principais
- **Produtor**: Script isolado que injeta código de interceção GraphQL nas páginas da Meta Ad Library.
- **Worker de IA**: Processo assíncrono (Celery) encarregado de descarregar os vídeos do Cloudflare R2 e processá-los via API Gemini, originando embeddings vetoriais de 768 dimensões.
- **Base de Dados**: PostgreSQL com pgvector e algoritmos de aproximação de vizinhos mais próximos (HNSW).
