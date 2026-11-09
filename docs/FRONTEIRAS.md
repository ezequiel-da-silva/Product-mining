# Fronteiras

Este documento separa o que a `main` faz do que continua fora do código. O rascunho de loja é só Shopify (`rascunho_shopify`). WooCommerce é um marcador de plataforma no host, ao lado de Yampi e Cartpanda. O cache que existe é o do embedding por hash de texto. Não há context cache do Gemini: a extração segue com `thinking_budget` 0.

## 1. Sessão da Biblioteca

O producer aceita `host.docker.internal` e uma aba de `https://www.facebook.com/ads/library`, ou o valor de `CDP_TARGET_URL`. `classificar_sessao` devolve `ok` com uma única aba da Biblioteca, `sessao_pedida` em checkpoint ou login, e `aba_ausente` nos outros casos. O estado fica em `sessao:estado` por 15 minutos. Só o estado `ok` publica anúncio. O beat, no intervalo do alerta da DLQ, manda uma linha para `ALERTA_WEBHOOK_URL`.

A retomada é humana, no navegador que a pessoa abriu. Não há cliente de AdsPower nem de Dolphin, nem `Add to Cart` sintético, nem rotina que aposenta perfil para contornar checkpoint.

## 2. Custo de aterrissagem

`margem_com_cogs` e `margem_em_brl` continuam olhando só venda e COGS. A margem líquida usa `parametro_viabilidade` e `faixa_frete_nicho`, gravadas pela migração `026`. A tarefa que preenche `venda_brl` e `cogs_brl` também grava `frete_estimado_brl`, `imposto_estimado_brl` e `margem_liquida_brl`. O rascunho Shopify só nasce com margem líquida positiva. Nicho sem faixa de frete fica com a margem vazia.

Os valores semeados de `ii`, `icms`, gateway e chargeback são parâmetros de exemplo. O operador troca a linha quando a regra mudar. `MARKUP_BRL` continua sendo o multiplicador do preço do rascunho.

## 3. Funil e concentração de lojas

`ler_funil` lê o pixel `fbq('init', …)` e as marcas de order bump no HTML que `buscar_html` já baixa. `loja_pixel` guarda `pixel_id` e `dominio`, únicos nos dois campos. `tem_order_bump` fica na oferta. O painel mostra o preço do bump quando o HTML traz `data-bump-price`. Sem esse valor, o carimbo existe e o ticket não ganha um número inventado.

O índice de lojas agrupa o host de `url_destino` nas canônicas dos últimos sete dias, com contagem de ofertas, soma de `vezes_avistado` e a primeira captura. É atividade, não faturamento.

Dois pedidos simulados, com boleto ou Pix sem pagamento, para ler o salto do número do pedido, são compra forjada. Não há rotina para isso.

## 4. Inferência

Oferta com vídeo novo segue para `processar_anuncio_multimodal` no Gemini 3.8 Flash. A herança por pHash e por narração parecida evita a segunda chamada do mesmo criativo. `gerar_angulos_em_teste` produz um ângulo para a oferta em `testando`.

`triagem_local` só entra se `MODELO_TRIAGEM_PATH` aponta para um arquivo. Sem o arquivo, o fluxo segue para o Gemini. Com confiança de pelo menos 0,90 em `generico` ou em rótulo fora do catálogo, a oferta fica `descartado_local` e não entra em `ai_multimodal_queue`. Abaixo de 0,90, segue para o Gemini.

A canônica estrelada recebe três peças em `variacao_copy`: headline, gancho de UGC e texto de stories. A chamada usa `thinking_budget` 0. Nada é publicado no gerenciador de anúncios.

## 5. Observabilidade e backup

Cada resultado de `avaliar_destino` entra em `egress:janela`. Com pelo menos 20 amostras e mais de 30% de bloqueio em cinco minutos, `egress:pausa` impede nova abertura por 15 minutos. A pausa não troca de proxy nem contorna o desafio. O alerta usa o webhook que já existe.

`src/metricas.py`, na rede `dados`, publica a cada 15 segundos `celery_fila_profundidade`, `pgbouncer_clientes_ativos`, `pgbouncer_servidores_usados` e `egress_bloqueio_ratio` em `:9108`. O Grafana fica de fora do Compose. O Flower continua sendo a vista operacional.

O Postgres do Compose permanece em `pgvector/pgvector:pg16`, sem `archive_mode`. O backup que existe é o `pg_dump` para dentro do volume. `docker/postgres-wal.Dockerfile` é a imagem filha com WAL-G, para um bucket R2 separado e sem direito de apagar. O primeiro `archive_mode=on` exige restart. Até esse restart, o `pg_dump` continua sendo o backup.
