import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from schemas import AnaliseAnuncioSchema


def test_analise_schema_sucesso():
    dados_mock = {
        "transcricao_video": "Compre agora este produto fantástico.",
        "gancho_3s": "Explosão na tela.",
        "nicho": "Utilidades",
        "gatilho_mental": "Urgência",
        "objecoes_quebradas": ["É caro", "Demora"],
        "preco_oferta_estimado": 199.90,
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
    assert esquema.preco_oferta_estimado == 199.90


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
        AnaliseAnuncioSchema(**{"nicho": "Saúde"})


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
