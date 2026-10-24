import logging
import math
import os
import secrets
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import streamlit as st
from core.secrets import ler_segredo
from google import genai
from google.genai import types
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

TITULO_PAINEL = "Painel Analítico: Mineração FB Ads"
TITULO_OFERTAS_RECENTES = "Ofertas concluídas mais recentes"
OFERTAS_RECENTES_LEGENDA = "Ofertas concluídas, da captura mais recente para a mais antiga."
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSAO_EMBEDDING = 768
PAGE_SIZE = 10
ROTULOS_OFERTAS = {
    "id": "Identificador",
    "nicho": "Nicho",
    "preco_oferta_estimado": "Preço estimado",
    "score": "Pontuação",
    "margem_estimada_pct": "Margem estimada",
    "data_ultima_captura": "Última captura",
    "similaridade_pct": "Similaridade",
}
FORMATO_MARGEM = "%.0f%%"
FORMATO_DATA = "DD/MM/YYYY HH:mm"

OFERTAS_RECENTES_SQL = """
    SELECT id, nicho, preco_oferta_estimado, score, margem_estimada_pct, data_ultima_captura
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
    ORDER BY data_ultima_captura DESC, id DESC
    LIMIT %s
"""

OFERTAS_RECENTES_CURSOR_SQL = """
    SELECT id, nicho, preco_oferta_estimado, score, margem_estimada_pct, data_ultima_captura
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND (data_ultima_captura, id) < (%s, %s)
    ORDER BY data_ultima_captura DESC, id DESC
    LIMIT %s
"""

OFERTAS_BUSCA_SQL = """
    SELECT id, nicho, preco_oferta_estimado, score, margem_estimada_pct, data_ultima_captura,
           embedding_analise <=> %s::vector AS distancia
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND embedding_analise IS NOT NULL
    ORDER BY embedding_analise <=> %s::vector, id
    LIMIT %s
"""

OFERTAS_BUSCA_CURSOR_SQL = """
    SELECT id, nicho, preco_oferta_estimado, score, margem_estimada_pct, data_ultima_captura,
           embedding_analise <=> %s::vector AS distancia
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND embedding_analise IS NOT NULL
      AND (embedding_analise <=> %s::vector, id) > (%s, %s)
    ORDER BY embedding_analise <=> %s::vector, id
    LIMIT %s
"""


def _require_dashboard_auth() -> None:
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        st.error("O dashboard não está configurado.")
        st.stop()
    if st.session_state.get("dashboard_authenticated"):
        return

    st.title(TITULO_PAINEL)
    password = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if secrets.compare_digest(password, expected):
            st.session_state["dashboard_authenticated"] = True
            st.rerun()
        st.error("Senha incorreta.")
    st.stop()


# Conexão segura (Apenas leitura)
@st.cache_resource
def init_connection():
    db_url = os.environ.get("POSTGRES_URL")
    return psycopg.connect(db_url, autocommit=True, row_factory=dict_row)


@st.cache_resource
def init_embedding_client():
    api_key = ler_segredo("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não configurada para a busca.")
    return genai.Client(api_key=api_key)


def get_connection():
    conn = init_connection()
    if conn.closed:
        init_connection.clear()
        conn = init_connection()
    return conn


def _vetor_embedding(resposta) -> list[float]:
    embeddings = getattr(resposta, "embeddings", None)
    if not embeddings:
        raise ValueError("A resposta de embedding não contém um vetor.")
    valores = getattr(embeddings[0], "values", None)
    if not valores:
        raise ValueError("A resposta de embedding não contém um vetor.")
    vetor = [float(valor) for valor in valores]
    if len(vetor) != DIMENSAO_EMBEDDING or not all(math.isfinite(valor) for valor in vetor):
        raise ValueError(f"Embedding inválido; a busca exige {DIMENSAO_EMBEDDING} dimensões finitas.")
    return vetor


def _normalizar_frase_busca(frase: str) -> str:
    return frase.strip().casefold()


@st.cache_data(ttl=3600, show_spinner=False)
def gerar_embedding_busca(frase: str) -> list[float]:
    resposta = init_embedding_client().models.embed_content(
        model=MODELO_EMBEDDING,
        contents=frase,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=DIMENSAO_EMBEDDING,
        ),
    )
    return _vetor_embedding(resposta)


def _vetor_para_pg(vetor: list[float]) -> str:
    if len(vetor) != DIMENSAO_EMBEDDING or not all(math.isfinite(valor) for valor in vetor):
        raise ValueError(f"O vetor da busca deve ter {DIMENSAO_EMBEDDING} dimensões finitas.")
    return f"[{','.join(format(valor, '.17g') for valor in vetor)}]"


def _limitar_pagina(ofertas: list[dict]) -> tuple[list[dict], bool]:
    return ofertas[:PAGE_SIZE], len(ofertas) > PAGE_SIZE


def consultar_ofertas_recentes(conn, cursor: tuple[object, int] | None = None) -> tuple[list[dict], bool]:
    if cursor is None:
        consulta = OFERTAS_RECENTES_SQL
        parametros = (PAGE_SIZE + 1,)
    else:
        consulta = OFERTAS_RECENTES_CURSOR_SQL
        parametros = (cursor[0], cursor[1], PAGE_SIZE + 1)
    with conn.cursor() as cur:
        cur.execute(consulta, parametros)
        return _limitar_pagina(cur.fetchall())


def consultar_ofertas_por_similaridade(
    conn,
    vetor: list[float],
    cursor: tuple[float, int] | None = None,
) -> tuple[list[dict], bool]:
    vetor_pg = _vetor_para_pg(vetor)
    if cursor is None:
        consulta = OFERTAS_BUSCA_SQL
        parametros = (vetor_pg, vetor_pg, PAGE_SIZE + 1)
    else:
        consulta = OFERTAS_BUSCA_CURSOR_SQL
        parametros = (vetor_pg, vetor_pg, cursor[0], cursor[1], vetor_pg, PAGE_SIZE + 1)
    with conn.cursor() as cur:
        cur.execute(consulta, parametros)
        return _limitar_pagina(cur.fetchall())


def formatar_preco_br(valor: object) -> str | None:
    if valor is None:
        return None
    quantizado = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    texto = f"{quantizado:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def ofertas_para_exibicao(ofertas: list[dict]) -> list[dict]:
    exibicao = []
    for oferta in ofertas:
        linha = dict(oferta)
        linha["preco_oferta_estimado"] = formatar_preco_br(linha.get("preco_oferta_estimado"))
        distancia = linha.pop("distancia", None)
        if distancia is not None:
            linha["similaridade_pct"] = max(0.0, min(100.0, (1.0 - float(distancia)) * 100.0))
        exibicao.append(linha)
    return exibicao


def colunas_ofertas_recentes(incluir_similaridade: bool = False):
    colunas = {
        "id": st.column_config.NumberColumn(ROTULOS_OFERTAS["id"], format="%d"),
        "nicho": st.column_config.TextColumn(ROTULOS_OFERTAS["nicho"]),
        "preco_oferta_estimado": st.column_config.TextColumn(ROTULOS_OFERTAS["preco_oferta_estimado"]),
        "score": st.column_config.NumberColumn(ROTULOS_OFERTAS["score"], format="%d"),
        "margem_estimada_pct": st.column_config.NumberColumn(
            ROTULOS_OFERTAS["margem_estimada_pct"],
            format=FORMATO_MARGEM,
        ),
        "data_ultima_captura": st.column_config.DatetimeColumn(
            ROTULOS_OFERTAS["data_ultima_captura"],
            format=FORMATO_DATA,
        ),
    }
    if incluir_similaridade:
        colunas["similaridade_pct"] = st.column_config.NumberColumn(
            ROTULOS_OFERTAS["similaridade_pct"],
            format=FORMATO_MARGEM,
        )
    return colunas


def _reiniciar_listagem(frase: str = "", vetor: list[float] | None = None) -> None:
    st.session_state["ofertas_modo"] = "busca" if vetor is not None else "recentes"
    st.session_state["ofertas_frase"] = frase
    st.session_state["ofertas_vetor"] = vetor
    st.session_state["ofertas_resultados"] = []
    st.session_state["ofertas_cursor"] = None
    st.session_state["ofertas_tem_mais"] = True
    st.session_state["ofertas_inicializadas"] = False


def _carregar_proxima_pagina(conn) -> None:
    modo = st.session_state["ofertas_modo"]
    cursor = st.session_state["ofertas_cursor"]
    if modo == "busca":
        ofertas, tem_mais = consultar_ofertas_por_similaridade(
            conn,
            st.session_state["ofertas_vetor"],
            cursor,
        )
    else:
        ofertas, tem_mais = consultar_ofertas_recentes(conn, cursor)

    st.session_state["ofertas_resultados"].extend(ofertas)
    if ofertas:
        ultima = ofertas[-1]
        if modo == "busca":
            st.session_state["ofertas_cursor"] = (float(ultima["distancia"]), ultima["id"])
        else:
            st.session_state["ofertas_cursor"] = (ultima["data_ultima_captura"], ultima["id"])
    st.session_state["ofertas_tem_mais"] = tem_mais
    st.session_state["ofertas_inicializadas"] = True


def main() -> None:
    st.set_page_config(page_title="Minerador Analítico", layout="wide")
    _require_dashboard_auth()
    st.title(TITULO_PAINEL)

    try:
        if "ofertas_modo" not in st.session_state:
            _reiniciar_listagem()

        with st.form("busca_semantica"):
            frase = st.text_input(
                "Buscar por nicho, promessa, gancho ou necessidade",
                value=st.session_state["ofertas_frase"],
            )
            buscar = st.form_submit_button("Buscar")

        if buscar:
            frase = _normalizar_frase_busca(frase)
            vetor = gerar_embedding_busca(frase) if frase else None
            _reiniciar_listagem(frase, vetor)

        conn = get_connection()
        if not st.session_state["ofertas_inicializadas"]:
            _carregar_proxima_pagina(conn)

        ofertas = st.session_state["ofertas_resultados"]
        if ofertas:
            em_busca = st.session_state["ofertas_modo"] == "busca"
            if em_busca:
                st.header(f'Resultados para "{st.session_state["ofertas_frase"]}"')
                st.caption("Ordenados por similaridade semântica.")
            else:
                st.header(TITULO_OFERTAS_RECENTES)
                st.caption(OFERTAS_RECENTES_LEGENDA)
            st.dataframe(
                ofertas_para_exibicao(ofertas),
                column_config=colunas_ofertas_recentes(incluir_similaridade=em_busca),
                use_container_width=True,
            )
            if st.session_state["ofertas_tem_mais"] and st.button("Carregar mais"):
                _carregar_proxima_pagina(conn)
                st.rerun()
        else:
            st.info("Nenhuma oferta concluída encontrada.")

    except Exception:
        logger.exception("Falha ao consultar ofertas no dashboard.")
        st.error("Não foi possível carregar as ofertas.")


if __name__ == "__main__":
    main()
