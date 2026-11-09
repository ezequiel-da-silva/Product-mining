# Fronteiras depois da 0.8.1

Este documento descreve o que a `main` faz hoje e o que ainda não está no código. As seções de desenho são proposta. Não há migração, tarefa Celery nem painel para elas.

A release 0.8.1 tem as migrações `001` a `025`. O rascunho de loja é só Shopify (`rascunho_shopify`). WooCommerce é um marcador de plataforma no host, ao lado de Yampi e Cartpanda. O cache que existe é o do embedding por hash de texto. Não há context cache do Gemini: a extração segue com `thinking_budget` 0. A margem continua sendo `(venda − COGS) / venda` quando as duas moedas coincidem. O producer entra numa única aba de CDP. O aquecimento do navegador continua manual.

## 1. Sessão da Biblioteca

**Hoje.** `src/producer.py` só aceita `host.docker.internal` e uma aba de `https://www.facebook.com/ads/library`, ou o valor de `CDP_TARGET_URL`. Não há cliente de AdsPower nem de Dolphin. Se a aba some ou a sessão cai no login, o producer levanta `RuntimeError` e a fila para sem distinguir captcha, checkpoint e queda de rede.

**Efeito.** Um perfil cansado interrompe a captura inteira. O tempo de aquecimento continua sendo trabalho da equipe.

**Fora do código.** Abrir perfil por API de anti-detect, disparar `Add to Cart` sintético e aposentar perfil para contornar checkpoint. O repositório não ganha esse farm.

**Desenho, ainda não implementado.** Tratar a perda da aba como pausa, com o mesmo webhook da DLQ.

```python
def classificar_sessao(urls: list[str]) -> str:
    biblioteca = [url for url in urls if url.startswith("https://www.facebook.com/ads/library")]
    if len(biblioteca) == 1:
        return "ok"
    if any("checkpoint" in url or "/login" in url for url in urls):
        return "sessao_pedida"
    return "aba_ausente"
```

O producer gravaria `sessao:estado` no Redis com TTL de 15 minutos e só publicaria anúncio quando o estado fosse `ok`. O Beat, no mesmo intervalo do alerta da DLQ, leria essa chave e mandaria uma linha para `ALERTA_WEBHOOK_URL`. A retomada continua humana, no navegador que a pessoa abriu.

## 2. Custo de aterrissagem

**Hoje.** `margem_com_cogs` e `margem_em_brl` só olham venda e COGS. `completar_brl` converte USD e CNY pela PTAX do dia, sem imposto, sem taxa de gateway e sem frete. `MARKUP_BRL` no rascunho Shopify é um multiplicador único em cima de `venda_brl`.

**Efeito.** Um produto com COGS baixo e frete pesado, ou com ICMS por dentro, aparece viável no painel e pode nascer como rascunho. O reajuste de 15% no fornecedor também ignora esses custos.

**Desenho, ainda não implementado.** A alíquota fica numa tabela. Os números abaixo são parâmetros de exemplo, não alíquota vigente.

```sql
CREATE TABLE IF NOT EXISTS parametro_viabilidade (
    nome VARCHAR(40) PRIMARY KEY,
    valor NUMERIC(12, 6) NOT NULL,
    vigente_em DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS faixa_frete_nicho (
    nicho VARCHAR(100) PRIMARY KEY,
    peso_gramas INTEGER NOT NULL CHECK (peso_gramas > 0),
    frete_brl NUMERIC(10, 2) NOT NULL CHECK (frete_brl >= 0)
);

INSERT INTO parametro_viabilidade (nome, valor) VALUES
    ('ii', 0.20),
    ('icms', 0.17),
    ('gateway_percentual', 0.0499),
    ('gateway_fixo_brl', 1.00),
    ('chargeback', 0.01)
ON CONFLICT (nome) DO NOTHING;
```

```python
def custo_liquido(venda: Decimal, cogs: Decimal, frete: Decimal, aliquota: dict[str, Decimal]) -> Decimal:
    base = cogs + frete
    ii = base * aliquota["ii"]
    icms = (base + ii) / (Decimal(1) - aliquota["icms"]) * aliquota["icms"]
    gateway = venda * aliquota["gateway_percentual"] + aliquota["gateway_fixo_brl"]
    chargeback = venda * aliquota["chargeback"]
    return (venda - cogs - frete - ii - icms - gateway - chargeback).quantize(Decimal("0.01"))
```

A tarefa que já preenche `venda_brl` e `cogs_brl` passaria a gravar `frete_estimado_brl`, `imposto_estimado_brl` e `margem_liquida_brl` na canônica. O rascunho Shopify só seguiria com margem líquida positiva. Nicho sem faixa de frete não receberia margem líquida e não viraria rascunho.

## 3. Funil e concentração de lojas

**Hoje.** Cada oferta tem `url_destino`, `page_id`, `vezes_avistado` e `tem_pixel`. O pixel é um booleano. O checkout só é lido para achar preço em JSON-LD, meta tag ou `products/{handle}.js`. Não há leitura de bump nem agrupamento de domínios.

**Efeito.** Duas lojas com o mesmo criativo e o mesmo pixel parecem anunciantes diferentes. O ticket exibido é o preço da página, mesmo quando o checkout empurra um segundo item.

**Fora do código.** Dois pedidos simulados, com boleto ou Pix sem pagamento, para ler o salto do número do pedido. Isso é compra forjada. Não há rotina para isso.

**Desenho, ainda não implementado.** O índice abaixo usa os anúncios já gravados. É atividade, não faturamento.

```sql
SELECT split_part(regexp_replace(url_destino, '^https?://', ''), '/', 1) AS dominio,
       COUNT(*) AS ofertas,
       SUM(vezes_avistado) AS avistamentos,
       MIN(data_primeira_captura) AS desde
FROM ofertas
WHERE oferta_canonica_id IS NULL
  AND data_ultima_captura >= CURRENT_TIMESTAMP - INTERVAL '7 days'
GROUP BY 1
ORDER BY avistamentos DESC
LIMIT 50;
```

O bump e o pixel sairiam do HTML que `buscar_html` já baixa, sem enviar o formulário de pagamento.

```python
_PIXEL = re.compile(r"""fbq\(\s*['"]init['"]\s*,\s*['"](\d{5,20})['"]""")
_BUMP = ("order-bump", "order_bump", "bump-item", "data-bump")

def ler_funil(html: str) -> dict:
    pixels = sorted(set(_PIXEL.findall(html or "")))
    texto = (html or "").casefold()
    return {"pixels": pixels, "tem_bump": any(marca in texto for marca in _BUMP)}
```

A migração criaria `loja_pixel (pixel_id, dominio)` com chave única nos dois campos. O mesmo `pixel_id` em dois domínios seria o agrupamento. `tem_order_bump` ficaria na oferta. O painel só somaria preço de bump ao ticket quando o HTML trouxesse um valor explícito.

## 4. Inferência

**Hoje.** Oferta com vídeo novo segue para `processar_anuncio_multimodal` no Gemini 3.8 Flash. A herança por pHash e por narração parecida evita a segunda chamada do mesmo criativo. `gerar_angulos_em_teste` produz um ângulo para a oferta marcada como `testando`, não um pacote de variações.

**Efeito.** O custo variável cresce com criativos inéditos. A equipe reescreve headline e stories à mão depois da aprovação.

**Desenho, ainda não implementado.** O worker de qualificação tem 1 CPU e concorrência 8. O modelo local só entraria se o arquivo existisse. Sem o arquivo, o fluxo atual seguiria inteiro.

```python
def triagem_local(texto: str, nicho_catalogo: set[str]) -> str:
    modelo = os.environ.get("MODELO_TRIAGEM_PATH", "").strip()
    if not modelo or not os.path.isfile(modelo):
        return "seguir"
    rotulo, confianca = prever(modelo, texto[:2000])
    if confianca >= 0.90 and rotulo == "generico":
        return "descartar"
    if confianca >= 0.90 and rotulo not in nicho_catalogo:
        return "descartar"
    return "seguir"
```

`prever` carregaria um FastText ou um ONNX quantizado uma vez por processo. Abaixo de 0,90 a oferta iria ao Gemini. O descarte gravaria `status_qualificacao = 'descartado_local'` e não enfileiraria `ai_multimodal_queue`.

Para a canônica aprovada, uma tarefa nova pediria três peças em JSON, com `thinking_budget` 0: headline, gancho de UGC e texto de stories. A entrada seria `copy_texto`, `gancho_3s` e as objeções já gravadas. A saída iria para `variacao_copy (oferta_id, peca, texto)`, única por oferta e peça. Nada seria publicado no gerenciador de anúncios.

## 5. Observabilidade e backup

**Hoje.** O backup documentado é um `pg_dump` para dentro do volume local. Não há exportador Prometheus, nem Grafana, nem `archive_mode`. O bloqueio de página espera 15 minutos por oferta e tenta de novo. Ninguém lê a taxa de 403, 429 e desafio na janela de cinco minutos. O PgBouncer expõe `SHOW POOLS` para `pipeline_writer`, e ninguém coleta.

**Efeito.** Um volume corrompido perde o banco até o último dump. Uma onda de desafio no proxy continua queimando o pool de egress, uma oferta a cada 15 minutos. A saturação do pool de 20 conexões só aparece quando o worker começa a esperar.

**Desenho, ainda não implementado.** Cada resultado de `avaliar_destino` entraria num sorted set no Redis. Bloqueio seria página `bloqueado` ou HTTP 403/429.

```python
def registrar_egress(cliente, bloqueou: bool) -> None:
    agora = time.time()
    cliente.zadd("egress:janela", {f"{agora}:{int(bloqueou)}:{uuid.uuid4().hex}": agora})
    cliente.zremrangebyscore("egress:janela", 0, agora - 300)
    membros = cliente.zrange("egress:janela", 0, -1)
    if len(membros) < 20:
        return
    bloqueios = sum(1 for item in membros if b":1:" in item)
    if bloqueios / len(membros) > 0.30:
        cliente.set("egress:pausa", "1", ex=900)
```

`buscar_html` consultaria `egress:pausa` antes do proxy. Com a chave presente, devolveria `None` e não abriria socket. A pausa expiraria em 15 minutos. Ela não troca de proxy nem contorna o desafio.

Um processo na rede `dados` leria, a cada 15 segundos, `LLEN` das filas Celery, `SHOW POOLS` e `SHOW STATS` no PgBouncer, e o tamanho de `egress:janela`. Publicaria em `:9108` as séries `celery_fila_profundidade`, `pgbouncer_clientes_ativos`, `pgbouncer_servidores_usados` e `egress_bloqueio_ratio`. O Grafana ficaria de fora do Compose. O Flower continuaria sendo a vista operacional.

A imagem `pgvector/pgvector:pg16` não traz WAL-G. O caminho seria uma imagem filha e estes parâmetros:

```text
wal_level = replica
archive_mode = on
archive_timeout = 60
archive_command = 'wal-g wal-push %p'
```

O ambiente do WAL-G apontaria para um bucket do R2 separado do bucket de vídeo, sem direito de apagar objetos. Um serviço diário rodaria `wal-g backup-push`. A restauração seria `wal-g backup-fetch` num volume novo, com `recovery.signal` e `restore_command = 'wal-g wal-fetch %f %p'`. O primeiro `archive_mode=on` exige restart do Postgres. Até lá, o `pg_dump` atual continua sendo o backup que existe.
