import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import time
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
LEGENDA_BUSCA = (
    "Esta busca filtra ofertas já analisadas. "
    "Para minerar anúncios novos, pesquise na aba do Chrome da Biblioteca de Anúncios."
)
PRECO_AUSENTE = "não identificado"
FILTRO_TODOS = "Todos"
FILTRO_NOTA = "Nota maior que 70"
NOTA_MINIMA = 70
BOTAO_DETALHES = "Ver detalhes"
LARGURA_VIDEO = 420
ALTURA_VIDEO = 280
GANCHO_LIMITE = 160
SESSAO_QUERY = "sessao"
SESSAO_SEGUNDOS = 12 * 60 * 60
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSAO_EMBEDDING = 768
PAGE_SIZE = 10
COLUNAS_OFERTA = """
    id, nicho, gancho_3s, gatilho_mental, url_destino, copy_texto,
    transcricao_video, url_video_r2, objecoes_quebradas, angulos_alternativos,
    preco_oferta_estimado, score, margem_estimada_pct, data_ultima_captura
"""
ROTULOS_OFERTAS = {
    "id": "Identificador",
    "nicho": "Nicho",
    "gancho_3s": "Gancho",
    "gatilho_mental": "Gatilho",
    "url_destino": "Link da oferta",
    "copy_texto": "Copy",
    "transcricao_video": "Transcrição",
    "url_video_r2": "Vídeo",
    "objecoes_quebradas": "Objeções",
    "angulos_alternativos": "Ângulos",
    "preco_oferta_estimado": "Preço estimado",
    "score": "Pontuação",
    "margem_estimada_pct": "Margem estimada",
    "data_ultima_captura": "Última captura",
    "similaridade_pct": "Similaridade",
}
# Bandit B608 is suppressed below because only this fixed column list is interpolated.
OFERTAS_RECENTES_SQL = (
    f"\n    SELECT {COLUNAS_OFERTA}"  # nosec B608
    """
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
    ORDER BY data_ultima_captura DESC, id DESC
    LIMIT %s
    """
)

OFERTAS_RECENTES_CURSOR_SQL = (
    f"\n    SELECT {COLUNAS_OFERTA}"  # nosec B608
    """
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND (data_ultima_captura, id) < (%s, %s)
    ORDER BY data_ultima_captura DESC, id DESC
    LIMIT %s
    """
)

OFERTAS_BUSCA_SQL = (
    f"\n    SELECT {COLUNAS_OFERTA},"  # nosec B608
    """
           embedding_analise <=> %s::vector AS distancia
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND embedding_analise IS NOT NULL
    ORDER BY embedding_analise <=> %s::vector, id
    LIMIT %s
    """
)

OFERTAS_BUSCA_CURSOR_SQL = (
    f"\n    SELECT {COLUNAS_OFERTA},"  # nosec B608
    """
           embedding_analise <=> %s::vector AS distancia
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND embedding_analise IS NOT NULL
      AND (embedding_analise <=> %s::vector, id) > (%s, %s)
    ORDER BY embedding_analise <=> %s::vector, id
    LIMIT %s
    """
)

NICHOS_CONCLUIDOS_SQL = """
    SELECT DISTINCT nicho
    FROM ofertas
    WHERE status_qualificacao = 'concluido'
      AND nicho IS NOT NULL
      AND btrim(nicho) <> ''
    ORDER BY nicho
"""


def _assinar_sessao(segredo: str, validade: int) -> str:
    mac = hmac.new(segredo.encode(), str(validade).encode(), hashlib.sha256).hexdigest()
    return f"{validade}.{mac}"


def _sessao_valida(token: str, segredo: str, agora: float | None = None) -> bool:
    if not token or not segredo or "." not in token:
        return False
    validade_texto, mac = token.split(".", 1)
    if not validade_texto.isdigit():
        return False
    validade = int(validade_texto)
    if validade <= (time.time() if agora is None else agora):
        return False
    esperada = hmac.new(segredo.encode(), validade_texto.encode(), hashlib.sha256).hexdigest()
    if len(mac) != len(esperada):
        return False
    return secrets.compare_digest(mac, esperada)


def _token_da_url() -> str:
    bruto = st.query_params.get(SESSAO_QUERY, "")
    if isinstance(bruto, (list, tuple)):
        return str(bruto[0]) if bruto else ""
    return str(bruto or "")


def _gravar_sessao(segredo: str) -> None:
    validade = int(time.time()) + SESSAO_SEGUNDOS
    st.query_params[SESSAO_QUERY] = _assinar_sessao(segredo, validade)


def _encerrar_sessao() -> None:
    st.session_state["dashboard_authenticated"] = False
    if SESSAO_QUERY in st.query_params:
        del st.query_params[SESSAO_QUERY]


def _require_dashboard_auth() -> None:
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        st.error("O dashboard não está configurado.")
        st.stop()
    if st.session_state.get("dashboard_authenticated"):
        return
    if _sessao_valida(_token_da_url(), expected):
        st.session_state["dashboard_authenticated"] = True
        return

    st.title(TITULO_PAINEL)
    password = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if secrets.compare_digest(password, expected):
            _gravar_sessao(expected)
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


def _clausula_filtro(filtro: str | None) -> tuple[str, tuple]:
    if not filtro or filtro == FILTRO_TODOS:
        return "", ()
    if filtro == FILTRO_NOTA:
        return "score > %s", (NOTA_MINIMA,)
    return "nicho = %s", (filtro,)


def _sql_com_filtro(consulta: str, clausula: str) -> str:
    if not clausula:
        return consulta
    marcador = "\n    ORDER BY"
    if marcador not in consulta:
        raise ValueError("A consulta filtrada precisa de ORDER BY.")
    return consulta.replace(marcador, f"\n      AND {clausula}{marcador}", 1)


def consultar_ofertas_recentes(
    conn,
    cursor: tuple[object, int] | None = None,
    filtro: str | None = None,
) -> tuple[list[dict], bool]:
    clausula, extra = _clausula_filtro(filtro)
    if cursor is None:
        consulta = _sql_com_filtro(OFERTAS_RECENTES_SQL, clausula)
        parametros = (*extra, PAGE_SIZE + 1)
    else:
        consulta = _sql_com_filtro(OFERTAS_RECENTES_CURSOR_SQL, clausula)
        parametros = (cursor[0], cursor[1], *extra, PAGE_SIZE + 1)
    with conn.cursor() as cur:
        cur.execute(consulta, parametros)
        return _limitar_pagina(cur.fetchall())


def consultar_ofertas_por_similaridade(
    conn,
    vetor: list[float],
    cursor: tuple[float, int] | None = None,
    filtro: str | None = None,
) -> tuple[list[dict], bool]:
    clausula, extra = _clausula_filtro(filtro)
    vetor_pg = _vetor_para_pg(vetor)
    if cursor is None:
        consulta = _sql_com_filtro(OFERTAS_BUSCA_SQL, clausula)
        parametros = (vetor_pg, *extra, vetor_pg, PAGE_SIZE + 1)
    else:
        consulta = _sql_com_filtro(OFERTAS_BUSCA_CURSOR_SQL, clausula)
        parametros = (vetor_pg, vetor_pg, cursor[0], cursor[1], *extra, vetor_pg, PAGE_SIZE + 1)
    with conn.cursor() as cur:
        cur.execute(consulta, parametros)
        return _limitar_pagina(cur.fetchall())


def listar_nichos_concluidos(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(NICHOS_CONCLUIDOS_SQL)
        return [str(linha["nicho"]) for linha in cur.fetchall() if linha.get("nicho")]


def formatar_preco_br(valor: object) -> str | None:
    if valor is None:
        return None
    quantizado = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    texto = f"{quantizado:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def texto_preco(valor: object) -> str:
    return formatar_preco_br(valor) or PRECO_AUSENTE


def similaridade_percentual(distancia: object) -> float:
    return max(0.0, min(100.0, (1.0 - float(distancia)) * 100.0))


def gancho_curto(texto: object, limite: int = GANCHO_LIMITE) -> str:
    if texto is None:
        return "—"
    limpo = str(texto).strip()
    if not limpo:
        return "—"
    if len(limpo) <= limite:
        return limpo
    return limpo[: limite - 1].rstrip() + "…"


def formatar_data_card(valor: object) -> str:
    if valor is None or not hasattr(valor, "strftime"):
        return "—"
    return valor.strftime("%d/%m/%Y %H:%M")


def _como_json(valor: object) -> object:
    if not isinstance(valor, str):
        return valor
    try:
        return json.loads(valor)
    except json.JSONDecodeError:
        return valor


def _url_http(valor: object) -> str | None:
    if isinstance(valor, str) and valor.startswith(("http://", "https://")):
        return valor
    return None


def _escrever_objecoes(valor: object) -> None:
    objecoes = _como_json(valor)
    if isinstance(objecoes, list) and objecoes:
        for item in objecoes:
            st.write(f"- {item}")
        return
    st.write("—")


def _escrever_angulos(valor: object) -> None:
    angulos = _como_json(valor)
    if not isinstance(angulos, list) or not angulos:
        st.write("—")
        return
    for angulo in angulos:
        if not isinstance(angulo, dict):
            st.write(str(angulo))
            continue
        titulo = angulo.get("titulo") or "Ângulo"
        foco = angulo.get("foco_emocional") or ""
        st.markdown(f"**{titulo}**" + (f" — {foco}" if foco else ""))
        st.write(angulo.get("copy_sugerida") or "—")


@st.dialog("Oferta", width="large")
def _dialogo_oferta(oferta: dict) -> None:
    st.subheader(oferta.get("nicho") or "Oferta")
    nota = oferta.get("score")
    nota_texto = f"{nota}/100" if nota is not None else "—"
    st.caption(
        f"{ROTULOS_OFERTAS['score']}: {nota_texto} · "
        f"{ROTULOS_OFERTAS['data_ultima_captura']}: {formatar_data_card(oferta.get('data_ultima_captura'))}"
    )
    video = _url_http(oferta.get("url_video_r2"))
    if video:
        st.html(
            "<style>"
            "[data-testid='stDialog'] video[data-testid='stVideo'] {"
            f"height: {ALTURA_VIDEO}px;"
            "width: 100%;"
            "object-fit: contain;"
            "background: #000;"
            "}"
            "</style>"
        )
        st.video(video, width=LARGURA_VIDEO)

    gatilho, margem, preco = st.columns(3)
    with gatilho:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["gatilho_mental"])
            st.write(oferta.get("gatilho_mental") or "—")
    with margem:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["margem_estimada_pct"])
            valor_margem = oferta.get("margem_estimada_pct")
            st.write(f"{valor_margem}%" if valor_margem is not None else "—")
    with preco:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["preco_oferta_estimado"])
            st.write(texto_preco(oferta.get("preco_oferta_estimado")))

    gancho, transcricao = st.columns(2, vertical_alignment="top")
    with gancho:
        with st.container(border=True, height="stretch"):
            st.markdown(f"**{ROTULOS_OFERTAS['gancho_3s']}**")
            st.write(oferta.get("gancho_3s") or "—")
    with transcricao:
        with st.container(border=True, height="stretch"):
            st.markdown(f"**{ROTULOS_OFERTAS['transcricao_video']}**")
            st.write(oferta.get("transcricao_video") or "—")

    with st.container(border=True):
        st.markdown(f"**{ROTULOS_OFERTAS['copy_texto']}**")
        st.write(oferta.get("copy_texto") or "—")

    objecoes_col, angulos_col = st.columns(2, vertical_alignment="top")
    with objecoes_col:
        with st.container(border=True, height="stretch"):
            st.markdown(f"**{ROTULOS_OFERTAS['objecoes_quebradas']}**")
            _escrever_objecoes(oferta.get("objecoes_quebradas"))
    with angulos_col:
        with st.container(border=True, height="stretch"):
            st.markdown(f"**{ROTULOS_OFERTAS['angulos_alternativos']}**")
            _escrever_angulos(oferta.get("angulos_alternativos"))

    url = _url_http(oferta.get("url_destino"))
    if url:
        st.link_button(ROTULOS_OFERTAS["url_destino"], url)


def _zerar_paginacao() -> None:
    st.session_state["ofertas_resultados"] = []
    st.session_state["ofertas_cursor"] = None
    st.session_state["ofertas_tem_mais"] = True
    st.session_state["ofertas_inicializadas"] = False


def _reiniciar_listagem(frase: str = "", vetor: list[float] | None = None) -> None:
    st.session_state["ofertas_modo"] = "busca" if vetor is not None else "recentes"
    st.session_state["ofertas_frase"] = frase
    st.session_state["ofertas_vetor"] = vetor
    _zerar_paginacao()


def _opcoes_filtro(nichos: list[str]) -> list[str]:
    return [FILTRO_TODOS, *nichos, FILTRO_NOTA]


def _sincronizar_filtro(opcoes: list[str]) -> str:
    if "filtro_ofertas" not in st.session_state or st.session_state["filtro_ofertas"] not in opcoes:
        st.session_state["filtro_ofertas"] = FILTRO_TODOS
    filtro = st.pills("Filtrar ofertas", opcoes, selection_mode="single", key="filtro_ofertas")
    if not filtro:
        filtro = FILTRO_TODOS
    if st.session_state.get("ofertas_filtro_visto") != filtro:
        if "ofertas_filtro_visto" in st.session_state:
            _zerar_paginacao()
        st.session_state["ofertas_filtro_visto"] = filtro
    return filtro


def _carregar_proxima_pagina(conn, filtro: str | None) -> None:
    modo = st.session_state["ofertas_modo"]
    cursor = st.session_state["ofertas_cursor"]
    if modo == "busca":
        ofertas, tem_mais = consultar_ofertas_por_similaridade(
            conn,
            st.session_state["ofertas_vetor"],
            cursor,
            filtro,
        )
    else:
        ofertas, tem_mais = consultar_ofertas_recentes(conn, cursor, filtro)

    st.session_state["ofertas_resultados"].extend(ofertas)
    if ofertas:
        ultima = ofertas[-1]
        if modo == "busca":
            st.session_state["ofertas_cursor"] = (float(ultima["distancia"]), ultima["id"])
        else:
            st.session_state["ofertas_cursor"] = (ultima["data_ultima_captura"], ultima["id"])
    st.session_state["ofertas_tem_mais"] = tem_mais
    st.session_state["ofertas_inicializadas"] = True


def _mostrar_cards(ofertas: list[dict], em_busca: bool) -> dict | None:
    escolhida = None
    for inicio in range(0, len(ofertas), 3):
        colunas = st.columns(3, vertical_alignment="top")
        for deslocamento, oferta in enumerate(ofertas[inicio : inicio + 3]):
            with colunas[deslocamento]:
                with st.container(border=True):
                    st.markdown(f"**{oferta.get('nicho') or '—'}**")
                    nota = oferta.get("score")
                    st.markdown(f"{ROTULOS_OFERTAS['score']}: {nota if nota is not None else '—'}")
                    if em_busca and oferta.get("distancia") is not None:
                        percentual = similaridade_percentual(oferta["distancia"])
                        st.caption(f"{ROTULOS_OFERTAS['similaridade_pct']}: {percentual:.0f}%")
                    st.write(gancho_curto(oferta.get("gancho_3s")))
                    st.caption(formatar_data_card(oferta.get("data_ultima_captura")))
                    if st.button(BOTAO_DETALHES, key=f"detalhe-{oferta.get('id')}", width="stretch"):
                        escolhida = oferta
    return escolhida


def main() -> None:
    st.set_page_config(page_title="Minerador Analítico", layout="wide")
    _require_dashboard_auth()
    titulo, sair = st.columns([6, 1])
    with titulo:
        st.title(TITULO_PAINEL)
    with sair:
        if st.button("Sair"):
            _encerrar_sessao()
            st.rerun()
    st.caption(LEGENDA_BUSCA)

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
        opcoes = _opcoes_filtro(listar_nichos_concluidos(conn))
        filtro = _sincronizar_filtro(opcoes)
        filtro_consulta = None if filtro == FILTRO_TODOS else filtro
        if not st.session_state["ofertas_inicializadas"]:
            _carregar_proxima_pagina(conn, filtro_consulta)

        ofertas = st.session_state["ofertas_resultados"]
        if ofertas:
            em_busca = st.session_state["ofertas_modo"] == "busca"
            if em_busca:
                st.header(f'Resultados para "{st.session_state["ofertas_frase"]}"')
                st.caption("Ordenados por similaridade semântica.")
            else:
                st.header(TITULO_OFERTAS_RECENTES)
                st.caption(OFERTAS_RECENTES_LEGENDA)
            escolhida = _mostrar_cards(ofertas, em_busca)
            if escolhida is not None:
                _dialogo_oferta(escolhida)
            if st.session_state["ofertas_tem_mais"] and st.button("Carregar mais"):
                _carregar_proxima_pagina(conn, filtro_consulta)
                st.rerun()
        else:
            st.info("Nenhuma oferta concluída encontrada.")

    except Exception:
        logger.exception("Falha ao consultar ofertas no dashboard.")
        st.error("Não foi possível carregar as ofertas.")


if __name__ == "__main__":
    main()
