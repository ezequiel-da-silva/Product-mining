# Software Design Document - Minerador FB

## Visão Geral

O sistema recolhe anúncios da Biblioteca do Facebook, qualifica a oferta, lê o preço da página de destino, agrupa criativos do mesmo produto e busca o custo de fábrica. O Gemini 3.8 Flash lê os primeiros 30 segundos do vídeo. O PostgreSQL 16 guarda a oferta e o embedding de 768 dimensões.

## Fluxo

1. O producer conecta por CDP a um navegador anti-detect e intercepta o GraphQL da biblioteca. A porta CDP fica no host, fora de `0.0.0.0`.
2. A qualificação calcula o score, sobe o vídeo ao Cloudflare R2 e guarda o pHash perceptual. Um criativo já analisado herda a análise da irmã e segue para a página desta oferta.
3. O worker de IA baixa o vídeo, chama o Gemini com as skills de `prompts/` e grava transcrição, nicho, objeções, preço falado, margem estimada e embedding. Falha de schema com análise já paga vai para `ai_dead_letter`.
4. A página de destino é lida com socket no IP público já resolvido. O preço só entra em BRL ou USD. Valor ambíguo é descartado.
5. Criativos do mesmo produto apontam para a oferta mais antiga. Essa canônica fica com `oferta_canonica_id` nulo. A triagem do grupo sobe para ela.
6. A oferta canônica, depois da leitura de preço, passa pela busca reversa de imagem no 1688 e no AliExpress. O resultado grava COGS, URL do fornecedor e até cinco depoimentos de 5 estrelas.
7. Quando a canônica está em teste e ainda não tem ângulo, o beat gera um único ângulo A/B.
8. Às 03:00 o lifecycle remove ofertas fracas e apaga do R2 o vídeo que nenhuma outra oferta referencia.
9. O painel Streamlit lê com `streamlit_readonly` e grava só `triagem_oferta`.

## Componentes

- **Produtor** (`src/producer.py`): Playwright sobre CDP e intercepção GraphQL.
- **Qualificação** (`src/worker_qualification.py`): score, upload, herança de criativo e beat de revisão.
- **Página** (`src/landing.py`, `src/comercial.py`): preço público, sem copiar o preço da irmã.
- **IA** (`src/worker_ia.py`, `prompts/`, `schemas.py`): extração multimodal, embedding e ângulo.
- **Grupo** (`src/agrupamento.py`): vizinhança por URL de vídeo, pHash e cosseno do embedding.
- **Fornecedor** (`src/sourcing.py`, `src/provas_sociais.py`, `src/fornecedor.py`): COGS e prova social.
- **Ciclo de vida** (`src/lifecycle.py`): decaimento e expiração.
- **Painel** (`src/dashboard.py`): triagem, busca vetorial e leitura do COGS.
- **Dados**: PostgreSQL 16, pgvector, partição mensal de `oferta_vetor`, Redis 7 e Celery.

## Filas

- `qualification_queue`: anúncio novo, revisão de preço, grupo e fornecedor.
- `ai_multimodal_queue`: análise do vídeo e ângulo de teste.
- `ai_dead_letter`: falha definitiva já paga. Não entra no worker padrão.
- `lifecycle_queue`: expiração noturna.

## Moeda e margem

O preço falado e o preço da página usam BRL ou USD. O COGS do 1688 é CNY e o do AliExpress é USD. A margem estimada do modelo só é substituída pelo cálculo `(venda - COGS) / venda` quando as duas moedas coincidem. Venda em BRL com custo em CNY permanece estimativa e o painel mostra os dois números.
