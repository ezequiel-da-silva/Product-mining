from typing import List, Optional

from pydantic import BaseModel, Field


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
    nicho: str = Field(
        description="Nicho de mercado categorizado (Ex: Saúde e Beleza, Pet, Casa, Gadgets, Infantil, Automotivo)."
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
