ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS tokens_prompt_ia INTEGER;

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS tokens_saida_ia INTEGER;
