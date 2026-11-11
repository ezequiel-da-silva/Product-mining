"""Regressão de prompt e frases que o SDD e o schema já contratam."""

import ast
from pathlib import Path

import yaml
from jinja2 import Template
from schemas import AnaliseAnuncioSchema

# Orçamento (len+3)//4 do prompt renderizado mais a instrução do YAML.
# É regressão de crescimento do texto, não o tokenizador do Gemini.
TETO_EXTRACAO = 1200
TETO_ANGULO = 900

RAIZ = Path(__file__).resolve().parents[1]


def _governanca():
    """Executa só a função, sem subir o cliente Gemini do worker."""
    fonte = (RAIZ / "src/worker_ia.py").read_text(encoding="utf-8")
    modulo = ast.parse(fonte)
    alvo = next(no for no in modulo.body if isinstance(no, ast.FunctionDef) and no.name == "_aplicar_governanca_prompt")
    namespace: dict = {}
    exec(ast.get_source_segment(fonte, alvo), namespace)  # nosec B102
    return namespace["_aplicar_governanca_prompt"]


def test_prompt_renderizado_cabe_no_teto_e_guarda_as_proibicoes():
    fonte = (RAIZ / "src/worker_ia.py").read_text(encoding="utf-8")
    assert "LIMITE_COPY_PROMPT = 2000" in fonte
    for trecho in (
        '_corte(oferta.get("nicho"), 100)',
        '_corte(oferta.get("sub_nicho"), 100)',
        '_corte(oferta.get("gancho_3s"), 500)',
        '_corte(oferta.get("gatilho_mental"), 200)',
        '_corte(oferta.get("transcricao_video"), 800)',
        '_corte(oferta.get("copy_texto"), 800)',
    ):
        assert trecho in fonte
    persona = yaml.safe_load((RAIZ / "prompts/system_persona.yaml").read_text(encoding="utf-8"))
    extracao = Template((RAIZ / "prompts/extracao_vsl.jinja").read_text(encoding="utf-8")).render(
        ad_copy_texto="a" * 2000
    )
    angulo = Template((RAIZ / "prompts/angulo_ab.jinja").read_text(encoding="utf-8")).render(
        nicho="n" * 100,
        sub_nicho="s" * 100,
        gancho="g" * 500,
        gatilho="t" * 200,
        fala="f" * 800,
        copy="c" * 800,
    )
    instrucao = persona["instruction"]
    angulo_instrucao = persona["angulo_instruction"]
    assert "Não invente fornecedor, custo de fábrica nem depoimento de cliente." in instrucao
    assert "Sem essa moeda, preço e moeda ficam nulos." in instrucao
    assert "Não invente preço, depoimento nem fornecedor." in angulo_instrucao
    assert "Não preencha fornecedor, COGS nem review." in extracao
    assert (len(instrucao) + len(extracao) + 3) // 4 <= TETO_EXTRACAO
    assert (len(angulo_instrucao) + len(angulo) + 3) // 4 <= TETO_ANGULO


def test_yaml_invalido_nao_troca_o_literal():
    aplicar = _governanca()
    assert aplicar({"model": "gemini-2.0", "temperature": 2}) == ("gemini-3.8-flash", 0.2)
    assert aplicar({"model": "gemini-3.8-flash", "temperature": 0.4})[1] == 0.4


def test_sdd_yaml_e_colunas_do_contrato():
    sdd = (RAIZ / "docs/SDD.md").read_text(encoding="utf-8")
    assert "768" in sdd
    assert "ai_dead_letter" in sdd
    assert "BRL ou USD" in sdd
    assert "partição mensal" in sdd
    assert "oferta_vetor" in sdd
    persona = (RAIZ / "prompts/system_persona.yaml").read_text(encoding="utf-8")
    assert 'model: "gemini-3.8-flash"' in persona
    sql = (RAIZ / "infra/init-db.sql").read_text(encoding="utf-8")
    migracoes = "\n".join(caminho.read_text(encoding="utf-8") for caminho in (RAIZ / "infra/migrations").glob("*.sql"))
    contrato = sql + "\n" + migracoes
    for coluna in (
        "transcricao_video",
        "gancho_3s",
        "nicho",
        "sub_nicho",
        "gatilho_mental",
        "tipo_angulo",
        "objecoes_quebradas",
        "preco_oferta_estimado",
        "preco_moeda",
        "margem_estimada_pct",
    ):
        assert coluna in AnaliseAnuncioSchema.model_fields
        assert coluna in contrato


def test_fronteiras_separa_o_que_fica_de_fora():
    texto = (RAIZ / "docs/FRONTEIRAS.md").read_text(encoding="utf-8")
    assert "rascunho_shopify" in texto
    assert "compra forjada" in texto
    assert "Não há context cache" in texto
    assert "sessao:estado" in texto
    assert "026" in texto
    sdd = (RAIZ / "docs/SDD.md").read_text(encoding="utf-8")
    assert "docs/FRONTEIRAS.md" in sdd
    assert "001" in sdd and "027" in sdd
