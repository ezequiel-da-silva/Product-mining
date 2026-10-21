CREATE INDEX IF NOT EXISTS idx_ofertas_concluidas_captura
    ON ofertas (data_ultima_captura DESC, id DESC)
    WHERE status_qualificacao = 'concluido';
