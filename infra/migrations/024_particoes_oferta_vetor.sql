-- Próximos meses de oferta_vetor e HNSW só na janela quente, na mesma transação.

CREATE OR REPLACE FUNCTION manter_particoes_oferta_vetor()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    inicio date;
    proximo date;
    nome text;
    particao regclass;
    relname text;
    mes date;
    corte date;
    idx text;
BEGIN
    corte := (
        date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo'))::date
        - INTERVAL '18 months'
    )::date;
    inicio := (
        date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo'))::date
        + INTERVAL '1 month'
    )::date;

    FOR passo IN 1..2 LOOP
        proximo := (inicio + INTERVAL '1 month')::date;
        nome := format('oferta_vetor_%s', to_char(inicio, 'YYYY_MM'));
        IF to_regclass(format('public.%I', nome)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF oferta_vetor FOR VALUES FROM (%L) TO (%L)',
                nome,
                inicio,
                proximo
            );
        END IF;
        inicio := proximo;
    END LOOP;

    FOR particao IN
        SELECT inhrelid
        FROM pg_inherits
        WHERE inhparent = 'public.oferta_vetor'::regclass
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_readonly') THEN
            EXECUTE format('GRANT SELECT ON TABLE %s TO streamlit_readonly', particao);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') THEN
            EXECUTE format('GRANT SELECT ON TABLE %s TO pipeline_writer', particao);
        END IF;
    END LOOP;

    IF to_regclass('public.idx_oferta_vetor_embedding') IS NOT NULL THEN
        EXECUTE 'DROP INDEX IF EXISTS public.idx_oferta_vetor_embedding';
    END IF;

    FOR particao IN
        SELECT inhrelid
        FROM pg_inherits
        WHERE inhparent = 'public.oferta_vetor'::regclass
    LOOP
        relname := replace(particao::text, '"', '');
        IF position('.' in relname) > 0 THEN
            relname := split_part(relname, '.', 2);
        END IF;
        idx := format('idx_%s_hnsw', relname);
        IF relname ~ '^oferta_vetor_[0-9]{4}_[0-9]{2}$' THEN
            mes := to_date(replace(substring(relname from '[0-9]{4}_[0-9]{2}$'), '_', '-'), 'YYYY-MM');
            IF mes >= corte THEN
                IF to_regclass(format('public.%I', idx)) IS NULL THEN
                    EXECUTE format(
                        'CREATE INDEX %I ON %s USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)',
                        idx,
                        particao
                    );
                END IF;
            ELSE
                EXECUTE format('DROP INDEX IF EXISTS public.%I', idx);
            END IF;
        ELSE
            EXECUTE format('DROP INDEX IF EXISTS public.%I', idx);
        END IF;
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION manter_particoes_oferta_vetor() FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifecycle_writer') THEN
        GRANT EXECUTE ON FUNCTION manter_particoes_oferta_vetor() TO lifecycle_writer;
    END IF;
END $$;
