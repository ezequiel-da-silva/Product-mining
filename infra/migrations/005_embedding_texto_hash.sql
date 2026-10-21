CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS embedding_texto_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_ofertas_embedding_texto_hash
    ON ofertas (embedding_texto_hash)
    WHERE embedding_analise IS NOT NULL;

UPDATE ofertas
SET embedding_texto_hash = encode(
    digest(
        convert_to(
            nicho || ' | ' || gatilho_mental || ' | ' || gancho_3s || ' | ' || left(transcricao_video, 500),
            'UTF8'
        ),
        'sha256'
    ),
    'hex'
)
WHERE embedding_analise IS NOT NULL
  AND embedding_texto_hash IS NULL
  AND nicho IS NOT NULL
  AND gatilho_mental IS NOT NULL
  AND gancho_3s IS NOT NULL
  AND transcricao_video IS NOT NULL;
