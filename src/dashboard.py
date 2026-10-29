import html
import json
import logging
import math
import os
import secrets
import time
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit

import psycopg
import streamlit as st
from core.pontuacao import NOTA_MINIMA_PAINEL
from core.secrets import ler_segredo
from google import genai
from google.genai import types
from psycopg.rows import dict_row
from schemas import NICHOS_CANONICOS, nicho_canonico
from src.midia_r2 import url_leitura

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
LEGENDA_ATALHOS = "Com o foco no card, S salva, D descarta e T marca testando."
ROTULO_SALVAR = "Salvar"
ROTULO_DESCARTAR = "Descartar"
ROTULO_TESTANDO = "Testando"
ESTADOS_TRIAGEM = ("estrelada", "descartada", "testando")
FILTRO_TODOS = "Todos"
FILTRO_NOTA = "Nota de 75 ou mais"
NOTA_MINIMA = NOTA_MINIMA_PAINEL
BOTAO_DETALHES = "Ver detalhes"
ESTIMATIVA_SOURCING = "Estimativa Sourcing (China)"
LEGENDA_MARGEM = "Markup padrão de sourcing na China. Não é conciliação fiscal."
LARGURA_VIDEO = 420
ALTURA_VIDEO = 280
GANCHO_LIMITE = 160
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSAO_EMBEDDING = 768
PAGE_SIZE = 10
LIMITE_EMBEDDING_POR_MINUTO = 12
JANELA_EMBEDDING_SEGUNDOS = 60.0
COLUNAS_OFERTA = """
    id, nicho, sub_nicho, gancho_3s, gatilho_mental, url_destino, copy_texto,
    transcricao_video, url_video_r2, objecoes_quebradas, angulos_alternativos,
    preco_oferta_estimado, preco_moeda, score, margem_estimada_pct, data_ultima_captura,
    dados_comerciais_ausentes
"""
ROTULOS_OFERTAS = {
    "id": "Identificador",
    "nicho": "Nicho",
    "sub_nicho": "Subnicho",
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


def _descartar_credencial_na_url() -> None:
    if "sessao" in st.query_params:
        del st.query_params["sessao"]


def _encerrar_sessao() -> None:
    st.session_state["dashboard_authenticated"] = False
    _descartar_credencial_na_url()


def _require_dashboard_auth() -> None:
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    _descartar_credencial_na_url()
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


def _clausula_filtro(filtro: str | None) -> tuple[str, tuple]:
    if not filtro or filtro == FILTRO_TODOS:
        return "", ()
    if filtro == FILTRO_NOTA:
        return "score >= %s", (NOTA_MINIMA,)
    return "nicho = %s", (filtro,)


def _sql_com_filtro(consulta: str, clausula: str, ocultar_descartadas: bool = True) -> str:
    partes = []
    if clausula:
        partes.append(clausula)
    if ocultar_descartadas:
        partes.append(
            "NOT EXISTS (SELECT 1 FROM triagem_oferta AS triagem "
            "WHERE triagem.oferta_id = ofertas.id AND triagem.estado = 'descartada')"
        )
    if not partes:
        return consulta
    marcador = "\n    ORDER BY"
    if marcador not in consulta:
        raise ValueError("A consulta filtrada precisa de ORDER BY.")
    extra = "\n      AND " + "\n      AND ".join(partes)
    return consulta.replace(marcador, f"{extra}{marcador}", 1)


def consultar_ofertas_recentes(
    conn,
    cursor: tuple[object, int] | None = None,
    filtro: str | None = None,
    ocultar_descartadas: bool = True,
) -> tuple[list[dict], bool]:
    clausula, extra = _clausula_filtro(filtro)
    if cursor is None:
        consulta = _sql_com_filtro(OFERTAS_RECENTES_SQL, clausula, ocultar_descartadas)
        parametros = (*extra, PAGE_SIZE + 1)
    else:
        consulta = _sql_com_filtro(OFERTAS_RECENTES_CURSOR_SQL, clausula, ocultar_descartadas)
        parametros = (cursor[0], cursor[1], *extra, PAGE_SIZE + 1)
    with conn.cursor() as cur:
        cur.execute(consulta, parametros)
        return _limitar_pagina(cur.fetchall())


def consultar_ofertas_por_similaridade(
    conn,
    vetor: list[float],
    cursor: tuple[float, int] | None = None,
    filtro: str | None = None,
    ocultar_descartadas: bool = True,
) -> tuple[list[dict], bool]:
    clausula, extra = _clausula_filtro(filtro)
    vetor_pg = _vetor_para_pg(vetor)
    if cursor is None:
        consulta = _sql_com_filtro(OFERTAS_BUSCA_SQL, clausula, ocultar_descartadas)
        parametros = (vetor_pg, *extra, vetor_pg, PAGE_SIZE + 1)
    else:
        consulta = _sql_com_filtro(OFERTAS_BUSCA_CURSOR_SQL, clausula, ocultar_descartadas)
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


def formatar_preco(valor: object, moeda: str) -> str | None:
    if valor is None:
        return None
    if moeda == "BRL":
        return formatar_preco_br(valor)
    if moeda == "USD":
        quantizado = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"US$ {quantizado:,.2f}"
    return None


def texto_preco(valor: object, moeda: str | None = None) -> str:
    if moeda in {"BRL", "USD"}:
        return formatar_preco(valor, moeda) or PRECO_AUSENTE
    if valor is None:
        return PRECO_AUSENTE
    return "Moeda não identificada"


def texto_nota(nota: object) -> str:
    if nota is None:
        return "—"
    return f"{nota}/100"


def rotulo_nicho(oferta: dict) -> str:
    nicho = str(oferta.get("nicho") or "").strip()
    sub = str(oferta.get("sub_nicho") or "").strip()
    if not nicho:
        return "—"
    if nicho == "Outros" and sub:
        return f"Outros: {sub}"
    return nicho


def rotulo_margem(valor: object, moeda: str | None = None) -> str:
    if moeda != "BRL":
        return "Margem não comparável"
    if valor is None:
        return "Margem não identificada"
    numero = int(valor)
    if numero >= 60:
        faixa = "alta"
    elif numero >= 40:
        faixa = "média"
    else:
        faixa = "baixa"
    return f"Margem {faixa}: {numero}%"


def _mostrar_margem(valor: object, moeda: str | None = None) -> None:
    st.caption(rotulo_margem(valor, moeda))
    st.badge(ESTIMATIVA_SOURCING, color="gray", help=LEGENDA_MARGEM)
    st.caption(LEGENDA_MARGEM)


def texto_literal(valor: object) -> str:
    """Impede que um texto persistido vire link, negrito ou código no rótulo."""
    texto = str(valor).replace("\\", "\\\\")
    for caractere in ("[", "]", "*", "_", "`"):
        texto = texto.replace(caractere, f"\\{caractere}")
    return texto.replace("://", ":\u2060//")


def rotulo_acessivel_detalhes(oferta: dict) -> str:
    nicho = str(oferta.get("nicho") or "").strip() or "sem nicho"
    identificador = oferta.get("id")
    numero = identificador if identificador is not None else "s/n"
    return f"{BOTAO_DETALHES} da oferta {texto_literal(nicho)} #{numero}"


def checkout_detectado(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    texto = url.lower()
    for marcador, nome in (
        ("shopify", "Shopify"),
        ("yampi", "Yampi"),
        ("cartpanda", "CartPanda"),
        ("nuvemshop", "Nuvemshop"),
        ("tiendanube", "Tiendanube"),
        ("woocommerce", "WooCommerce"),
    ):
        if marcador in texto:
            return nome
    return None


def texto_bloqueio_cota() -> str:
    return (
        f"Limite de {LIMITE_EMBEDDING_POR_MINUTO} buscas com embedding por minuto. "
        "Aguarde a janela liberar antes de tentar de novo."
    )


def texto_regiao_viva(
    *,
    busca_limitada: bool,
    total: int,
    em_busca: bool,
    frase: str,
    tem_mais: bool,
) -> str:
    if busca_limitada:
        return texto_bloqueio_cota()
    if total <= 0:
        return "Nenhuma oferta concluída encontrada."
    extras = " Há mais ofertas." if tem_mais else ""
    if em_busca:
        return f'{total} ofertas para "{frase}".{extras}'
    if total == 1:
        return f"1 oferta concluída.{extras}"
    return f"{total} ofertas concluídas.{extras}"


def html_regiao_viva(texto: str) -> str:
    return '<div data-regiao-viva role="status" aria-live="polite" aria-atomic="true">' f"{html.escape(texto)}</div>"


def _html_atalhos_triagem() -> str:
    mapa = f'{{s: "{ROTULO_SALVAR}", d: "{ROTULO_DESCARTAR}", t: "{ROTULO_TESTANDO}"}}'
    return f"""
<style>
div[data-testid="stHtml"]:has(script[data-atalhos-triagem]) {{
  height: 0;
  overflow: hidden;
  margin: 0;
  padding: 0;
}}
</style>
<script data-atalhos-triagem>
(() => {{
  const mapa = {mapa};
  const cardDoFoco = (alvo) => {{
    let no = alvo;
    while (no && no !== document.body) {{
      if (no.querySelectorAll && no.querySelectorAll("[data-oferta-card]").length === 1) return no;
      no = no.parentElement;
    }}
    return null;
  }};
  const anterior = window.__atalhosTriagem;
  if (anterior) {{
    document.removeEventListener("keydown", anterior.tecla, true);
    document.removeEventListener("pointerdown", anterior.ponteiro, true);
  }}
  let cardApontado = null;
  const ponteiro = (evento) => {{
    const alvo = evento.target instanceof Element ? evento.target : null;
    cardApontado = alvo ? cardDoFoco(alvo) : null;
  }};
  const tecla = (evento) => {{
    if (evento.repeat || evento.metaKey || evento.ctrlKey || evento.altKey) return;
    const alvo = evento.target instanceof Element ? evento.target : document.activeElement;
    if (!(alvo instanceof Element)) return;
    const tag = (alvo.tagName || "").toLowerCase();
    if (tag === "textarea" || alvo.isContentEditable) return;
    if (tag === "input") {{
      const tipo = (alvo.type || "").toLowerCase();
      if (tipo !== "checkbox" && tipo !== "radio") return;
    }}
    const rotulo = mapa[(evento.key || "").toLowerCase()];
    if (!rotulo) return;
    const peloFoco = cardDoFoco(alvo) || (document.activeElement instanceof Element ? cardDoFoco(document.activeElement) : null);
    const controleForaDoCard = !peloFoco && (tag === "button" || tag === "a" || tag === "select");
    const card = peloFoco || (controleForaDoCard ? null : cardApontado);
    if (!card) return;
    const botao = [...card.querySelectorAll("button")].find(
      (item) => (item.textContent || "").replace(/\\s+/g, " ").trim() === rotulo
    );
    if (!botao) return;
    evento.preventDefault();
    botao.click();
  }};
  window.__atalhosTriagem = {{tecla, ponteiro}};
  document.addEventListener("pointerdown", ponteiro, true);
  document.addEventListener("keydown", tecla, true);
}})();
</script>
"""


def _registrar_atalhos_triagem() -> None:
    st.html(_html_atalhos_triagem(), unsafe_allow_javascript=True)


def texto_comercial(oferta: dict) -> str:
    if oferta.get("dados_comerciais_ausentes"):
        return "Dados comerciais ausentes"
    return texto_preco(oferta.get("preco_oferta_estimado"), oferta.get("preco_moeda"))


def aviso_destino_inseguro(url: str) -> str | None:
    partes = urlsplit(url.strip())
    if partes.scheme.lower() != "http":
        return None
    host = partes.hostname or "domínio externo"
    return f"O destino {host} trafega por HTTP, sem criptografia, e pertence a um domínio externo."


def _link_loja(url: str) -> None:
    seguro = html.escape(url, quote=True)
    aviso = aviso_destino_inseguro(url)
    titulo = html.escape(aviso, quote=True) if aviso else "Abrir a loja em uma nova aba"
    st.markdown(
        f'<a href="{seguro}" target="_blank" rel="noopener noreferrer" title="{titulo}">Abrir loja</a>',
        unsafe_allow_html=True,
    )
    if aviso:
        st.caption(aviso)


def _avaliar_cota_embedding(
    historico: list[float],
    agora: float,
    limite: int = LIMITE_EMBEDDING_POR_MINUTO,
    janela: float = JANELA_EMBEDDING_SEGUNDOS,
) -> tuple[bool, list[float]]:
    recentes = [marca for marca in historico if agora - marca < janela]
    if len(recentes) >= limite:
        return False, recentes
    return True, [*recentes, agora]


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
            st.text(str(angulo))
            continue
        titulo = str(angulo.get("titulo") or "Ângulo")
        foco = str(angulo.get("foco_emocional") or "").strip()
        linha = f"{titulo} — {foco}" if foco else titulo
        st.text(linha)
        st.write(angulo.get("copy_sugerida") or "—")


def _dialogo_oferta(oferta: dict) -> None:
    titulo = rotulo_acessivel_detalhes(oferta)

    @st.dialog(titulo, width="large")
    def _conteudo(oferta: dict) -> None:
        _corpo_dialogo(oferta)

    _conteudo(oferta)


def _corpo_dialogo(oferta: dict) -> None:
    st.text(rotulo_nicho(oferta))
    nota = oferta.get("score")
    st.caption(
        f"{ROTULOS_OFERTAS['score']}: {texto_nota(nota)} · "
        f"{ROTULOS_OFERTAS['data_ultima_captura']}: {formatar_data_card(oferta.get('data_ultima_captura'))}"
    )
    video = url_leitura(oferta.get("url_video_r2"))
    if video:
        st.caption("Prévia do vídeo do anúncio")
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
    with st.expander("Transcrição do Criativo"):
        st.write(oferta.get("transcricao_video") or "—")

    gatilho, margem, preco = st.columns(3)
    with gatilho:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["gatilho_mental"])
            st.write(oferta.get("gatilho_mental") or "—")
    with margem:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["margem_estimada_pct"])
            _mostrar_margem(oferta.get("margem_estimada_pct"), oferta.get("preco_moeda"))
    with preco:
        with st.container(border=True):
            st.caption(ROTULOS_OFERTAS["preco_oferta_estimado"])
            st.write(texto_comercial(oferta))

    with st.container(border=True):
        st.markdown(f"**{ROTULOS_OFERTAS['gancho_3s']}**")
        st.write(oferta.get("gancho_3s") or "—")

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
        _link_loja(url)


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
    nomes: list[str] = []
    for nome in (*NICHOS_CANONICOS, *nichos):
        if nome and nome not in nomes:
            nomes.append(nome)
    return [FILTRO_TODOS, *nomes, FILTRO_NOTA]


def _aplicar_busca(frase: str, agora: float | None = None) -> None:
    normalizada = _normalizar_frase_busca(frase)
    nicho = nicho_canonico(normalizada)
    if nicho:
        st.session_state["filtro_ofertas"] = nicho
        st.session_state["busca_limitada"] = False
        _reiniciar_listagem()
        return
    if not normalizada:
        st.session_state["busca_limitada"] = False
        _reiniciar_listagem()
        return
    instante = time.monotonic() if agora is None else agora
    permitido, historico = _avaliar_cota_embedding(
        list(st.session_state.get("embedding_buscas_em", [])),
        instante,
    )
    st.session_state["embedding_buscas_em"] = historico
    if not permitido:
        st.session_state["busca_limitada"] = True
        return
    st.session_state["busca_limitada"] = False
    _reiniciar_listagem(normalizada, gerar_embedding_busca(normalizada))


def _sincronizar_filtro(opcoes: list[str]) -> str:
    if "filtro_ofertas" not in st.session_state or st.session_state["filtro_ofertas"] not in opcoes:
        st.session_state["filtro_ofertas"] = FILTRO_TODOS
    filtro = st.pills(
        "Filtrar ofertas",
        opcoes,
        selection_mode="single",
        key="filtro_ofertas",
        format_func=texto_literal,
    )
    if not filtro:
        filtro = FILTRO_TODOS
    if st.session_state.get("ofertas_filtro_visto") != filtro:
        if "ofertas_filtro_visto" in st.session_state:
            _zerar_paginacao()
        st.session_state["ofertas_filtro_visto"] = filtro
    return filtro


def _carregar_proxima_pagina(conn, filtro: str | None, ocultar_descartadas: bool = True) -> None:
    modo = st.session_state["ofertas_modo"]
    cursor = st.session_state["ofertas_cursor"]
    if modo == "busca":
        ofertas, tem_mais = consultar_ofertas_por_similaridade(
            conn,
            st.session_state["ofertas_vetor"],
            cursor,
            filtro,
            ocultar_descartadas,
        )
    else:
        ofertas, tem_mais = consultar_ofertas_recentes(conn, cursor, filtro, ocultar_descartadas)

    st.session_state["ofertas_resultados"].extend(ofertas)
    if ofertas:
        ultima = ofertas[-1]
        if modo == "busca":
            st.session_state["ofertas_cursor"] = (float(ultima["distancia"]), ultima["id"])
        else:
            st.session_state["ofertas_cursor"] = (ultima["data_ultima_captura"], ultima["id"])
    st.session_state["ofertas_tem_mais"] = tem_mais
    st.session_state["ofertas_inicializadas"] = True


def _legenda_triagem(estado: object) -> None:
    if estado == "estrelada":
        st.caption("Salva")
    elif estado == "descartada":
        st.caption("Descartada")
    elif estado == "testando":
        st.caption("Em teste")


def _mostrar_cards(ofertas: list[dict], em_busca: bool, triagem: dict[int, str]) -> dict | None:
    escolhida = None
    for inicio in range(0, len(ofertas), 3):
        colunas = st.columns(3, vertical_alignment="top")
        for deslocamento, oferta in enumerate(ofertas[inicio : inicio + 3]):
            with colunas[deslocamento]:
                with st.container(border=True):
                    nicho = rotulo_nicho(oferta)
                    oferta_id = oferta.get("id")
                    if isinstance(oferta_id, int) and not isinstance(oferta_id, bool):
                        st.html(f'<span data-oferta-card="{oferta_id}" hidden></span>')
                        st.checkbox(
                            f"Selecionar a oferta {texto_literal(nicho)} #{oferta_id}",
                            key=f"selecionar-{oferta_id}",
                        )
                    st.text(nicho)
                    st.caption(texto_comercial(oferta))
                    _mostrar_margem(oferta.get("margem_estimada_pct"), oferta.get("preco_moeda"))
                    checkout = checkout_detectado(oferta.get("url_destino"))
                    if checkout:
                        st.caption(f"Checkout: {checkout}")
                    st.markdown(f"{ROTULOS_OFERTAS['score']}: {texto_nota(oferta.get('score'))}")
                    if em_busca and oferta.get("distancia") is not None:
                        percentual = similaridade_percentual(oferta["distancia"])
                        st.caption(f"{ROTULOS_OFERTAS['similaridade_pct']}: {percentual:.0f}%")
                    _legenda_triagem(triagem.get(oferta_id))
                    st.write(gancho_curto(oferta.get("gancho_3s")))
                    st.caption(formatar_data_card(oferta.get("data_ultima_captura")))
                    url = _url_http(oferta.get("url_destino"))
                    if url:
                        _link_loja(url)
                    if st.button(rotulo_acessivel_detalhes(oferta), key=f"detalhe-{oferta_id}", width="stretch"):
                        escolhida = {"tipo": "detalhe", "oferta": oferta}
                    salvar, descartar, testar = st.columns(3)
                    with salvar:
                        if st.button(ROTULO_SALVAR, key=f"salvar-{oferta_id}"):
                            escolhida = {"tipo": "triagem", "id": oferta_id, "estado": "estrelada"}
                    with descartar:
                        if st.button(ROTULO_DESCARTAR, key=f"descartar-{oferta_id}"):
                            escolhida = {"tipo": "triagem", "id": oferta_id, "estado": "descartada"}
                    with testar:
                        if st.button(ROTULO_TESTANDO, key=f"testando-{oferta_id}"):
                            escolhida = {"tipo": "triagem", "id": oferta_id, "estado": "testando"}
    return escolhida


SQL_TRIAGEM = """
SELECT oferta_id, estado
FROM triagem_oferta
WHERE oferta_id = ANY(%s)
"""

SQL_DEFINIR_TRIAGEM = """
INSERT INTO triagem_oferta (oferta_id, estado)
VALUES (%s, %s)
ON CONFLICT (oferta_id) DO UPDATE
SET estado = EXCLUDED.estado,
    atualizado_em = CURRENT_TIMESTAMP
"""

SQL_REMOVER_TRIAGEM = "DELETE FROM triagem_oferta WHERE oferta_id = %s AND estado = %s"

SQL_OPERACAO_STATUS = """
SELECT status_qualificacao, COUNT(*) AS total
FROM ofertas
GROUP BY status_qualificacao
ORDER BY status_qualificacao
"""

SQL_OPERACAO_FALHAS = "SELECT COUNT(*) AS total FROM ofertas WHERE falhas_ia > 0"

SQL_OPERACAO_TOKENS = """
SELECT COALESCE(SUM(tokens_prompt_ia), 0) AS prompt,
       COALESCE(SUM(tokens_saida_ia), 0) AS saida
FROM ofertas
WHERE data_ultima_captura >= date_trunc('month', CURRENT_TIMESTAMP)
"""


def _triagem_por_oferta(conn, ofertas: list[dict]) -> dict[int, str]:
    ids = [oferta["id"] for oferta in ofertas if oferta.get("id") is not None]
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(SQL_TRIAGEM, (ids,))
        return {linha["oferta_id"]: linha["estado"] for linha in cur.fetchall()}


def _definir_triagem(conn, oferta_id: int, estado: str) -> None:
    with conn.cursor() as cur:
        cur.execute(SQL_REMOVER_TRIAGEM, (oferta_id, estado))
        if cur.rowcount == 0:
            cur.execute(SQL_DEFINIR_TRIAGEM, (oferta_id, estado))
    conn.commit()


SQL_DEFINIR_TRIAGEM_LOTE = """
INSERT INTO triagem_oferta (oferta_id, estado)
SELECT id, %(estado)s
FROM unnest(%(ids)s::bigint[]) AS id
ON CONFLICT (oferta_id) DO UPDATE
SET estado = EXCLUDED.estado,
    atualizado_em = CURRENT_TIMESTAMP
"""


def _ids_selecionados(ofertas: list[dict]) -> list[int]:
    ids: list[int] = []
    for oferta in ofertas:
        oferta_id = oferta.get("id")
        if not isinstance(oferta_id, int) or isinstance(oferta_id, bool):
            continue
        if st.session_state.get(f"selecionar-{oferta_id}"):
            ids.append(oferta_id)
    return ids


def _definir_triagem_lote(conn, oferta_ids: list[int], estado: str) -> None:
    ids = [item for item in oferta_ids if isinstance(item, int) and not isinstance(item, bool)]
    if not ids or estado not in ESTADOS_TRIAGEM:
        return
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(SQL_DEFINIR_TRIAGEM_LOTE, {"ids": ids, "estado": estado})


def _aplicar_lote(conn, ofertas: list[dict]) -> bool:
    selecionadas = _ids_selecionados(ofertas)
    if not selecionadas:
        return False
    st.caption(f"{len(selecionadas)} ofertas selecionadas")
    salvar, descartar, testar = st.columns(3)
    estado = None
    with salvar:
        if st.button("Salvar seleção", key="lote-salvar"):
            estado = "estrelada"
    with descartar:
        if st.button("Descartar seleção", key="lote-descartar"):
            estado = "descartada"
    with testar:
        if st.button("Testando seleção", key="lote-testando"):
            estado = "testando"
    if estado is None:
        return False
    _definir_triagem_lote(conn, selecionadas, estado)
    for oferta_id in selecionadas:
        st.session_state[f"selecionar-{oferta_id}"] = False
    return True


def _painel_operacao(conn) -> None:
    st.header("Operação do pipeline")
    st.caption("Contagens da fila e tokens de IA gravados neste mês.")
    with conn.cursor() as cur:
        cur.execute(SQL_OPERACAO_STATUS)
        status = cur.fetchall()
        cur.execute(SQL_OPERACAO_FALHAS)
        falhas = cur.fetchone()
        cur.execute(SQL_OPERACAO_TOKENS)
        tokens = cur.fetchone()
    if status:
        colunas = st.columns(len(status))
        for coluna, linha in zip(colunas, status, strict=False):
            with coluna:
                st.metric(str(linha["status_qualificacao"]), int(linha["total"]))
    else:
        st.info("Nenhuma oferta na base.")
    st.metric("Ofertas com falha de IA", int((falhas or {}).get("total") or 0))
    prompt = int((tokens or {}).get("prompt") or 0)
    saida = int((tokens or {}).get("saida") or 0)
    st.caption(f"Tokens neste mês: {prompt} de prompt e {saida} de resposta.")


def _titulo_lista(em_busca: bool) -> None:
    if em_busca:
        st.header(f'Resultados para "{st.session_state["ofertas_frase"]}"')
        st.caption("Ordenados por similaridade semântica.")
        return
    st.header(TITULO_OFERTAS_RECENTES)
    st.caption(OFERTAS_RECENTES_LEGENDA)


def _painel_ofertas(conn) -> None:
    if "ofertas_modo" not in st.session_state:
        _reiniciar_listagem()
    with st.form("busca_semantica"):
        frase = st.text_input(
            "Buscar por nicho, promessa, gancho ou necessidade",
            value=st.session_state["ofertas_frase"],
        )
        buscar = st.form_submit_button("Buscar")
    mostrar_descartadas = st.checkbox("Mostrar descartadas", key="mostrar_descartadas")
    if st.session_state.get("descartadas_vistas") != mostrar_descartadas:
        if "descartadas_vistas" in st.session_state:
            _zerar_paginacao()
        st.session_state["descartadas_vistas"] = mostrar_descartadas

    if buscar:
        _aplicar_busca(frase)
    if st.session_state.get("busca_limitada"):
        st.warning(texto_bloqueio_cota())

    opcoes = _opcoes_filtro(listar_nichos_concluidos(conn))
    filtro = _sincronizar_filtro(opcoes)
    filtro_consulta = None if filtro == FILTRO_TODOS else filtro
    ocultar_descartadas = not mostrar_descartadas
    if not st.session_state["ofertas_inicializadas"]:
        _carregar_proxima_pagina(conn, filtro_consulta, ocultar_descartadas)

    ofertas = st.session_state["ofertas_resultados"]
    em_busca = st.session_state["ofertas_modo"] == "busca"
    anuncio = texto_regiao_viva(
        busca_limitada=bool(st.session_state.get("busca_limitada")),
        total=len(ofertas),
        em_busca=em_busca,
        frase=str(st.session_state.get("ofertas_frase") or ""),
        tem_mais=bool(st.session_state.get("ofertas_tem_mais")),
    )
    st.html(html_regiao_viva(anuncio))
    if not ofertas:
        st.info("Nenhuma oferta concluída encontrada.")
        return

    _titulo_lista(em_busca)
    st.caption(LEGENDA_ATALHOS)
    _registrar_atalhos_triagem()
    if _aplicar_lote(conn, ofertas):
        st.rerun()
    triagem = _triagem_por_oferta(conn, ofertas)
    escolhida = _mostrar_cards(ofertas, em_busca, triagem)
    if escolhida and escolhida["tipo"] == "detalhe":
        _dialogo_oferta(escolhida["oferta"])
    elif escolhida and escolhida["tipo"] == "triagem":
        _definir_triagem(conn, int(escolhida["id"]), escolhida["estado"])
        st.rerun()
    if st.session_state["ofertas_tem_mais"] and st.button("Carregar mais"):
        _carregar_proxima_pagina(conn, filtro_consulta, ocultar_descartadas)
        st.rerun()


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
        conn = get_connection()
        aba_ofertas, aba_operacao = st.tabs(["Ofertas", "Operação"])
        with aba_ofertas:
            _painel_ofertas(conn)
        with aba_operacao:
            _painel_operacao(conn)
    except Exception:
        logger.exception("Falha ao consultar ofertas no dashboard.")
        st.error("Não foi possível carregar as ofertas.")


if __name__ == "__main__":
    main()
