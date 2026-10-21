ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS processamento_iniciado_em TIMESTAMPTZ;
