# Skills da IA

O Gemini 3.8 Flash tem duas skills. O custo de fábrica e os depoimentos não passam por modelo: a qualificação lê a página pública do 1688 ou do AliExpress.

O schema de cada skill está em `schemas.py`. Um campo novo no schema precisa da mesma regra no prompt. O worker não chama o modelo de novo para corrigir nicho: `nicho_canonico` encaixa um sinônimo já pago no catálogo e, se não achar, grava `Outros` com o texto original em `sub_nicho`.

## Extração do anúncio

| | |
| --- | --- |
| System instruction | `prompts/system_persona.yaml`, chave `instruction` |
| Prompt do usuário | `prompts/extracao_vsl.jinja` |
| Schema | `AnaliseAnuncioSchema` |
| Modelo | `gemini-3.8-flash`, temperatura 0,2, `thinking_budget` 0 |
| Mídia | primeiros 30 segundos do vídeo no R2 |

Regras que a skill e o validador aplicam:

- Nicho é um nome do catálogo: Saúde e Beleza, Pet, Casa, Gadgets, Infantil, Automotivo, Moda, Fitness, Utilidades, Alimentação ou Outros.
- `sub_nicho` só permanece quando o nicho é Outros.
- Preço só é gravado com moeda BRL ou USD dita no vídeo ou na copy. Sem essa moeda, preço e moeda ficam nulos.
- `margem_estimada_pct` é estimativa de 0 a 100 para sourcing na China. Não é o COGS medido.
- A skill não inventa fornecedor, custo de fábrica nem depoimento. Esses campos vêm da busca reversa por imagem.

A copy entra no template já cortada em 2.000 caracteres. Se o arquivo Jinja não estiver no disco, o worker usa o mesmo roteiro embutido em `_obter_prompt_final`.

O embedding de 768 dimensões usa `gemini-embedding-2` sobre nicho, gatilho, gancho e os primeiros 500 caracteres da transcrição. A busca do painel reutiliza o vetor da frase na paginação.

Uma resposta que não valida o schema não volta para a fila multimodal. O payload pago segue para `ai_dead_letter` e só sai de lá por `reprocessar_falha_definitiva`.

## Ângulo de teste A/B

| | |
| --- | --- |
| System instruction | `prompts/system_persona.yaml`, chave `angulo_instruction` |
| Prompt do usuário | `prompts/angulo_ab.jinja` |
| Schema | `GeracaoAnguloSchema` |
| Quando roda | oferta canônica em triagem `testando`, ainda sem ângulo |

A skill devolve um único ângulo, com título, foco emocional e copy curta. O texto recebido é cortado antes do template: nicho e subnicho em 100 caracteres, gatilho em 200, gancho em 500, fala e copy em 800. A skill não inventa preço, depoimento nem fornecedor.

## O que fica fora do modelo

A cada dois minutos, `tasks.qualification.revisar_concluidas` consulta até cinco ofertas canônicas que já tiveram a página de destino lida e ainda não têm `fornecedor_consultado_em`.

- A imagem é `url_imagem_criativo` ou, na falta dela, a `og:image` da página de destino. As duas precisam ser `http` ou `https` num host público.
- O 1688 responde em CNY. O AliExpress responde em USD. Havendo os dois, fica o menor preço entre os três primeiros do 1688 com similaridade de pelo menos 0,82.
- A página do produto escolhido fornece até cinco depoimentos com nota exata de 5, texto ou foto de cliente.
- A margem estimada só é substituída quando a venda e o COGS estão na mesma moeda.
- A consulta é registrada mesmo quando o marketplace devolve uma página sem preço. Outra tentativa exige limpar `fornecedor_consultado_em`.
