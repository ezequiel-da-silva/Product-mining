import unicodedata
from typing import List, Literal, Optional, get_args

from pydantic import BaseModel, Field, create_model, model_validator

_CAMPOS_PROIBIDOS = {"fornecedor", "cogs", "cogs_estimado", "depoimento", "provas_sociais", "foto_url"}

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

TipoAngulo = Literal[
    "dor_problema",
    "demonstracao",
    "prova_social_ugc",
    "comparativo",
]
TIPOS_ANGULO: tuple[str, ...] = get_args(TipoAngulo)


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
    tipo: TipoAngulo = Field(
        description=(
            "Arquétipo deste ângulo. dor_problema é sofrimento sem a solução. "
            "demonstracao é usabilidade e transformação. prova_social_ugc é depoimento nativo. "
            "comparativo é antes e depois ou método antigo contra o novo."
        )
    )
    titulo: str = Field(description="Título curto do ângulo de venda (ex: 'Foco na Economia de Tempo').")
    foco_emocional: str = Field(description="O gatilho emocional exato (ex: Vaidade, Medo, Praticidade).")
    copy_sugerida: str = Field(description="Sugestão de um novo texto de anúncio curto (Copy) usando este ângulo.")


def schema_geracao_angulo(original: str | None) -> type[BaseModel]:
    """Com arquétipo original, o schema só aceita um dos outros três."""
    if original not in TIPOS_ANGULO:
        return GeracaoAnguloSchema
    opostos = tuple(tipo for tipo in TIPOS_ANGULO if tipo != original)
    tipo_fechado = Literal.__getitem__(opostos)
    angulo = create_model(
        "AnguloOposto",
        tipo=(tipo_fechado, Field(description="Arquétipo diferente do anúncio original.")),
        titulo=(str, Field(description="Título curto do ângulo de venda.")),
        foco_emocional=(str, Field(description="O gatilho emocional exato.")),
        copy_sugerida=(str, Field(description="Sugestão de um novo texto de anúncio curto.")),
    )
    return create_model(
        "GeracaoAnguloOposto",
        angulos_alternativos=(
            List[angulo],  # type: ignore[valid-type]
            Field(
                description="Um ângulo de arquétipo diferente do original.",
                min_length=1,
                max_length=1,
            ),
        ),
    )


class GeracaoAnguloSchema(BaseModel):
    angulos_alternativos: List[AnguloVenda] = Field(
        description="Gere 1 ângulo de venda, o mais forte, para um teste A/B.",
        min_length=1,
        max_length=1,
    )


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
    tipo_angulo: TipoAngulo = Field(
        description=(
            "Arquétipo do criativo original. dor_problema foca o sofrimento ou a ineficiência sem a solução. "
            "demonstracao foca usabilidade, funcionamento e transformação. "
            "prova_social_ugc é depoimento, unboxing ou selfie review. "
            "comparativo é antes e depois, economia ou método tradicional contra o novo."
        )
    )
    objecoes_quebradas: List[str] = Field(
        description="Lista de objeções do comprador que o anúncio tenta quebrar (ex: frete, confiança, tempo de resultado)."
    )
    preco_oferta_estimado: Optional[float] = Field(
        description="Preço numérico do produto (apenas números), se for mencionado no vídeo ou na copy. Nulo se não houver.",
        default=None,
    )
    preco_moeda: Optional[str] = Field(
        default=None,
        max_length=8,
        description="Moeda ISO do preço. Só BRL ou USD são gravados; sem moeda o preço é descartado.",
    )
    margem_estimada_pct: Optional[int] = Field(
        description="Estimativa da margem bruta de lucro em porcentagem (0 a 100), considerando sourcing na China (AliExpress/1688).",
        default=None,
        ge=0,
        le=100,
    )

    @model_validator(mode="before")
    @classmethod
    def rejeitar_campos_de_fornecedor(cls, dados):
        if isinstance(dados, dict) and _CAMPOS_PROIBIDOS.intersection(dados):
            raise ValueError("A análise não aceita fornecedor, COGS nem depoimento.")
        return dados

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

    @model_validator(mode="after")
    def descartar_preco_sem_moeda(self) -> "AnaliseAnuncioSchema":
        moeda = (self.preco_moeda or "").strip().upper()
        if self.preco_oferta_estimado is None or moeda not in {"BRL", "USD"}:
            self.preco_oferta_estimado = None
            self.preco_moeda = None
            return self
        self.preco_moeda = moeda
        return self


class PecasVariacaoSchema(BaseModel):
    headline: str = Field(min_length=1, description="Headline de impacto com gancho de curiosidade ou benefício direto.")
    ugc: str = Field(min_length=1, description="Roteiro de 30 segundos em primeira pessoa, estilo depoimento nativo.")
    stories: str = Field(min_length=1, description="Copy sequencial em 3 telas para Stories ou Reels, com CTA claro.")


class ParecerJuizSchema(BaseModel):
    nicho: NichoCanonico = Field(description="Nicho canônico validado.")
    motivo: str = Field(description="Justificativa sucinta para o enquadramento.")
