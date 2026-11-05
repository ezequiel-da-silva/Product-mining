# Critérios de Avaliação do Agente de IA

As skills estão em `docs/SKILLS_IA.md`. Esta lista é o que uma amostra manual confere.

## Extração

1. **Preço**. `preco_oferta_estimado` só existe quando o vídeo ou a copy dizem o valor e a moeda BRL ou USD. Sem essa moeda, preço e moeda ficam nulos. Preço de outra loja irmã não entra nesta etapa.
2. **Nicho**. O valor gravado é um nome do catálogo. Sinônimo reconhecido vira o nome canônico. Texto fora do catálogo vira `Outros`, com a frase original em `sub_nicho`.
3. **Margem**. `margem_estimada_pct` fica entre 0 e 100 e representa a estimativa de sourcing na China. Ela não é o COGS lido no 1688 ou no AliExpress.
4. **Fora da skill**. Fornecedor, custo e depoimento de cliente não saem do modelo. Se a resposta inventar esses dados, o schema não tem onde gravá-los.
5. **Janela**. Transcrição e gancho cobrem os primeiros 30 segundos. O gancho descreve os 3 primeiros.
6. **Schema**. JSON inválido ou campo fora do contrato não volta para a fila multimodal. A análise paga segue para `ai_dead_letter`.

## Ângulo

1. Só a oferta canônica em estado `testando`, ainda sem ângulo, recebe a skill.
2. A resposta traz um único ângulo, com título, foco emocional e copy curta.
3. A copy sugerida não inventa preço, depoimento nem fornecedor.

## Amostra

Toda semana, revisar 5 vídeos com score de 80 ou mais e 5 ofertas que receberam COGS. A primeira amostra calibra `prompts/system_persona.yaml` e `prompts/extracao_vsl.jinja`. A segunda confere se o COGS e os depoimentos batem com a página do fornecedor, não com a fala do anúncio.
