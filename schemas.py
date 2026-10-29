import unicodedata
from typing import List, Literal, Optional, get_args

from pydantic import BaseModel, Field, model_validator

# Categorias fechadas de D2C/dropshipping. "Outros" exige sub_nicho descritivo.
NichoCanonico = Literal[
    "Saúde e Beleza",
    "Pet",
    "Casa",
    "Gadgets",
    "Infantil",
    "Automotivo",
    "Moda",
    "Fitness",
    "Utilidades",
    "Alimentação",
    "Outros",
]
NICHOS_CANONICOS: tuple[str, ...] = get_args(NichoCanonico)


def _chave_nicho(valor: str) -> str:
    decomposicao = unicodedata.normalize("NFKD", valor.strip().casefold())
    sem_acento = "".join(caractere for caractere in decomposicao if not unicodedata.combining(caractere))
    return " ".join(sem_acento.split())


_NICHOS_POR_CHAVE = {_chave_nicho(nome): nome for nome in NICHOS_CANONICOS}


def nicho_canonico(frase: str) -> str | None:
    """Devolve o nicho do catálogo quando a frase coincide com o nome, sem acento e sem caixa."""
    if not frase or not frase.strip():
        return None
    return _NICHOS_POR_CHAVE.get(_chave_nicho(frase))


class AnguloVenda(BaseModel):
    titulo: str = Field(description="Título curto do ângulo de venda (ex: 'Foco na Economia de Tempo').")
    foco_emocional: str = Field(description="O gatilho emocional exato (ex: Vaidade, Medo, Praticidade).")
    copy_sugerida: str = Field(description="Sugestão de um novo texto de anúncio curto (Copy) usando este ângulo.")


class AnaliseAnuncioSchema(BaseModel):
    transcricao_video: str = Field(
        description="Transcrição apenas da locução e dos diálogos dos primeiros 30 segundos do vídeo."
    )
    gancho_3s: str = Field(
        description="Análise dos 3 primeiros segundos: o que acontece visualmente e o que é falado para reter a atenção."
    )
    nicho: NichoCanonico = Field(
        description=(
            "Nicho canônico de D2C/dropshipping. Use exatamente um destes valores: "
            "Saúde e Beleza, Pet, Casa, Gadgets, Infantil, Automotivo, Moda, Fitness, "
            "Utilidades, Alimentação ou Outros. Não invente sinônimos (Pet Shop, Cachorro, Beleza)."
        )
    )
    sub_nicho: Optional[str] = Field(
        default=None,
        max_length=100,
        description=(
            "Obrigatório somente quando nicho for Outros. Descreva a categoria livre "
            "(ex.: Pet Shop, Cachorro, Gatos). Deixe nulo nos demais nichos."
        ),
    )
    gatilho_mental: str = Field(
        description="O gatilho mental predominante usado na Copy ou no Vídeo (Ex: Dor/Problema Agudo, Prova Social, Urgência)."
    )
    objecoes_quebradas: List[str] = Field(
        description="Lista de objeções do comprador que o anúncio tenta quebrar (ex: frete, confiança, tempo de resultado)."
    )
    preco_oferta_estimado: Optional[float] = Field(
        description="Preço numérico do produto (apenas números), se for mencionado no vídeo ou na copy. Nulo se não houver.",
        default=None,
    )
    margem_estimada_pct: Optional[int] = Field(
        description="Estimativa da margem bruta de lucro em porcentagem (0 a 100), considerando sourcing na China (AliExpress/1688).",
        default=None,
        ge=0,
        le=100,
    )
    angulos_alternativos: List[AnguloVenda] = Field(
        description="Gere 1 ângulo de venda, o mais forte, para um teste A/B futuro.",
        max_length=1,
    )

    @model_validator(mode="after")
    def exigir_sub_nicho_em_outros(self) -> "AnaliseAnuncioSchema":
        sub = (self.sub_nicho or "").strip()
        if self.nicho == "Outros":
            if not sub:
                raise ValueError("sub_nicho descreve a categoria quando o nicho é Outros")
            self.sub_nicho = sub
            return self
        self.sub_nicho = None
        return self
