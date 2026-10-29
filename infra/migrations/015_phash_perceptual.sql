-- Identidade exata continua em phash_criativo (SHA-256).
-- phash_perceptual aproxima espelho, reencode e marca d'água.

ALTER TABLE ofertas
    ADD COLUMN IF NOT EXISTS phash_perceptual BIT(64);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
        GRANT UPDATE (phash_perceptual) ON TABLE ofertas TO pipeline_writer;
    END IF;
END $$;
