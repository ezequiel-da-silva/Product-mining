import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from schemas import NICHOS_CANONICOS, AnaliseAnuncioSchema, nicho_canonico


def test_analise_schema_sucesso():
    dados_mock = {
        "transcricao_video": "Compre agora este produto fantástico.",
        "gancho_3s": "Explosão na tela.",
        "nicho": "Utilidades",
        "gatilho_mental": "Urgência",
        "objecoes_quebradas": ["É caro", "Demora"],
        "preco_oferta_estimado": 199.90,
        "preco_moeda": "brl",
        "margem_estimada_pct": 50,
        "angulos_alternativos": [
            {
                "titulo": "Praticidade",
                "foco_emocional": "Economia de tempo",
                "copy_sugerida": "Poupe 3 horas do seu dia.",
            }
        ],
    }
    esquema = AnaliseAnuncioSchema(**dados_mock)
    assert esquema.nicho == "Utilidades"
    assert esquema.sub_nicho is None
    assert esquema.preco_oferta_estimado == 199.90
    assert esquema.preco_moeda == "BRL"


def test_analise_schema_rejeita_nicho_livre():
    dados = {
        "transcricao_video": "Compre agora.",
        "gancho_3s": "Produto na mão.",
        "nicho": "Pet Shop",
        "gatilho_mental": "Urgência",
        "objecoes_quebradas": [],
        "angulos_alternativos": [],
    }
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**dados)


def test_analise_schema_outros_exige_sub_nicho():
    dados = {
        "transcricao_video": "Compre agora.",
        "gancho_3s": "Produto na mão.",
        "nicho": "Outros",
        "gatilho_mental": "Urgência",
        "objecoes_quebradas": [],
        "angulos_alternativos": [],
    }
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**dados)

    esquema = AnaliseAnuncioSchema(**dados, sub_nicho="  Pet Shop  ")
    assert esquema.nicho == "Outros"
    assert esquema.sub_nicho == "Pet Shop"


def test_nicho_canonico_ignora_caixa_e_acento():
    assert nicho_canonico("  PET ") == "Pet"
    assert nicho_canonico("saude e beleza") == "Saúde e Beleza"
    assert nicho_canonico("pet shop") is None
    assert nicho_canonico("") is None
    assert "Outros" in NICHOS_CANONICOS


def test_analise_schema_descarta_preco_ambiguo():
    dados = {
        "transcricao_video": "Custa cento e noventa.",
        "gancho_3s": "Produto na mão.",
        "nicho": "Casa",
        "gatilho_mental": "Preço",
        "objecoes_quebradas": [],
        "preco_oferta_estimado": 199.90,
        "angulos_alternativos": [],
    }
    sem_moeda = AnaliseAnuncioSchema(**dados)
    assert sem_moeda.preco_oferta_estimado is None
    assert sem_moeda.preco_moeda is None

    euro = AnaliseAnuncioSchema(**dados, preco_moeda="EUR")
    assert euro.preco_oferta_estimado is None
    assert euro.preco_moeda is None


def test_analise_schema_rejeita_mais_de_um_angulo():
    dados = {
        "transcricao_video": "Fala dos primeiros 30 segundos.",
        "gancho_3s": "Produto na mão.",
        "nicho": "Casa",
        "gatilho_mental": "Praticidade",
        "objecoes_quebradas": [],
        "angulos_alternativos": [
            {"titulo": "Um", "foco_emocional": "Medo", "copy_sugerida": "Copy 1"},
            {"titulo": "Dois", "foco_emocional": "Vaidade", "copy_sugerida": "Copy 2"},
        ],
    }

    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**dados)


def test_analise_schema_falha_campo_obrigatorio():
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**{"nicho": "Casa"})


def test_analise_schema_margem_invalida():
    dados_base = {
        "transcricao_video": "Teste",
        "gancho_3s": "Gancho",
        "nicho": "Casa",
        "gatilho_mental": "Escassez",
        "objecoes_quebradas": [],
        "angulos_alternativos": [],
    }
    # Margem acima de 100 deve falhar
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**dados_base, margem_estimada_pct=150)

    # Margem negativa deve falhar
    with pytest.raises(ValidationError):
        AnaliseAnuncioSchema(**dados_base, margem_estimada_pct=-10)
