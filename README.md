# Minerador de Produtos Avançado (Facebook Ads) 🚀

Este projeto é uma arquitetura de nível Enterprise para extração, qualificação, enriquecimento e armazenamento de anúncios validados do Facebook Ads. Utiliza microsserviços, inteligência artificial multimodal (Gemini 3.8 Flash e `gemini-embedding-2`), fila de tarefas assíncronas e busca vetorial (HNSW) para criar um SaaS autônomo e resiliente.

## 🏗️ Arquitetura e Stack Tecnológico

- **Producer (Extrator):** Python + Playwright conectado via CDP a navegadores Anti-Detect. Interceptação direta de tráfego GraphQL (Zero Fingerprint) e mascaramento de automação (`playwright-stealth`).
- **Message Broker:** Redis 7 + Celery para roteamento estrito e concorrência balanceada. Monitoramento via Flower.
- **Storage Relacional e Vetorial:** PostgreSQL 16 com extensão `pgvector` e busca otimizada HNSW.
- **Workers:** Qualificação, página de destino, agrupamento, busca de fornecedor, worker multimodal (Gemini + Jinja2) e ciclo de vida noturno.
- **CDN Privada:** Cloudflare R2 atuando como intermediário para evitar perda de mídia ("Link Rot") da Meta.
- **Frontend / RAG:** Interface analítica reativa em Streamlit conectada a uma role `readonly` no banco, prevenindo injeções Text-to-SQL.
- **DevSecOps & Qualidade:** Hooks de pré-commit (`pre-commit`), Linter ultra-rápido (`Ruff`), análise estática de segurança (`Bandit`) e testes unitários (`pytest`).

## 📁 Estrutura do Projeto

```text
product_mining/
├── .github/                   # Pipelines de CI/CD (Linter e Segurança)
├── core/
│   └── celery_app.py          # Configuração do Celery e roteamento de filas
├── docker/
│   ├── producer.Dockerfile    # Imagem do serviço de extração (Playwright)
│   ├── streamlit.Dockerfile   # Imagem do painel analítico
│   └── worker.Dockerfile      # Imagem base para os workers de IA e Qualificação
├── docs/                      # SDD, skills da IA, acessibilidade e avaliação do agente
├── infra/
│   ├── init-db.sql            # DDL do PostgreSQL, índices pgvector e RBAC
│   └── migrations/            # Migrações idempotentes aplicadas por apply-migrations.sh
├── prompts/                   # Skills do Gemini: persona, extração e ângulo A/B
├── src/
│   ├── producer.py            # Interceptação via Playwright CDP
│   ├── worker_qualification.py
│   ├── worker_ia.py           # Gemini, embedding e ângulo de teste
│   ├── landing.py             # Preço público da página de destino
│   ├── agrupamento.py         # Criativos do mesmo produto
│   ├── sourcing.py            # COGS por busca reversa de imagem
│   ├── provas_sociais.py      # Depoimentos de 5 estrelas
│   └── dashboard.py           # Painel Streamlit
├── tests/                     # Suite de testes unitários (pytest)
├── .env.example               # Template das variáveis de ambiente
├── .pre-commit-config.yaml    # Configuração de ganchos de segurança e formatação
├── docker-compose.yml         # Orquestração de todos os microsserviços
├── pyproject.toml             # Configurações do Ruff (Linter/Formatter)
├── requirements.txt           # Dependências globais do projeto em Python
└── schemas.py                 # Esquemas estritos Pydantic para saída da IA

```

## ⚙️ Pré-requisitos

1. **Docker Engine** (>= 20.10) e **Docker Compose** instalados.
2. Navegador Anti-Detect (AdsPower, Dolphin{anty}, etc.) rodando na máquina hospedeira.
   O producer conecta pelo CDP em `host.docker.internal` e na porta configurada. Não exponha
   essa porta em `0.0.0.0`: o protocolo CDP controla o navegador, inclusive sessões e cookies.
   Use um perfil anti-detect vazio, sem conta pessoal. Escute somente em `127.0.0.1` quando o
   acesso vier de um túnel, ou somente no endereço da bridge Docker quando o contêiner precisar
   alcançar o host. Bloqueie a porta para o restante da rede.


3. Python 3.11+ para execução de testes e linters locais.
4. Chaves de API ativas: Gemini AI Studio e Cloudflare R2.

## 🚀 Setup e Deploy Inicial

1. **Configuração de Variáveis de Ambiente:**
Copie o arquivo de exemplo e preencha com suas credenciais reais:
```bash
cp .env.example .env

```


2. **Ativar Qualidade de Código (Local):**
Crie um ambiente virtual, instale as dependências e ative os hooks:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pre-commit pytest
pre-commit install

```

O `pre-commit install` instala os hooks `pre-commit` e `commit-msg` (Conventional Commits).
Em um clone que já tinha os hooks, rode `pre-commit install` de novo para ativar o `commit-msg`.


3. **Inicialização do Cluster:**
Com o `.env` configurado e o navegador Anti-Detect aberto, faça o build e suba os contêineres
num volume novo. Em um volume que já existe, siga antes a ordem de
"Atualizar um volume já existente" abaixo:
```bash
docker compose up -d --build

```

PostgreSQL e Redis não são publicados no host. Dashboard (`127.0.0.1:8501`) e Flower
(`127.0.0.1:5555`) pedem as credenciais `DASHBOARD_PASSWORD` e `FLOWER_BASIC_AUTH`.
Para TLS fora da máquina local, coloque um proxy reverso na frente desses painéis.

### Atualizar um volume já existente

Os scripts em `/docker-entrypoint-initdb.d` só rodam na primeira inicialização do volume, e o
código novo pode depender de colunas criadas pelas migrações. Por isso, atualize o schema
antes de subir as imagens novas, sempre sem `down -v`:

```bash
# .env com todas as senhas, incluindo LIFECYCLE_DB_PASSWORD
docker compose up -d --no-deps --force-recreate postgres
docker compose exec -T postgres /docker-entrypoint-initdb.d/zz-readonly-role.sh
docker compose exec -T postgres /docker-entrypoint-initdb.d/zz-pipeline-role.sh
docker compose exec -T postgres /docker-entrypoint-initdb.d/zz-lifecycle-role.sh
./infra/apply-migrations.sh
docker compose up -d --build
```

Cada migração roda com `lock_timeout` de 5 s (ajustável em `MIGRATION_LOCK_TIMEOUT`). Se uma
delas não conseguir o lock, o script para com erro, nada daquela migração é gravado e as
anteriores continuam válidas. Nesse caso, rode `./infra/apply-migrations.sh` de novo e não
suba o código novo enquanto ele não terminar sem erro.

Para trocar a senha de `pipeline_writer`, `lifecycle_writer` ou `streamlit_readonly` em um
volume que já existe, aplique o `ALTER ROLE` antes de recriar os serviços. A ordem inversa
derruba a conexão até o recreate. Não use `docker compose down -v`: isso apaga o volume
`postgres_data`.

```bash
./infra/rotate-role-password.sh pipeline_writer 'senha-nova'
./infra/rotate-role-password.sh lifecycle_writer 'senha-nova'
./infra/rotate-role-password.sh streamlit_readonly 'senha-nova'
```

O script aplica o `ALTER ROLE` e, para `pipeline_writer` e `lifecycle_writer`, reescreve
`/tmp/pgbouncer/userlist.txt` no contêiner e manda `SIGHUP` ao PgBouncer. Sem esse passo o
Postgres aceita a senha nova e o PgBouncer continua na antiga: o worker recebe `auth failed`.
`streamlit_readonly` não passa pelo PgBouncer.

Grave a mesma senha em `PIPELINE_DB_PASSWORD`, `LIFECYCLE_DB_PASSWORD` ou
`STREAMLIT_DB_PASSWORD` no `.env` antes de recriar o `spy_pgbouncer`. O arquivo vive no
tmpfs e o próximo start gera o userlist de novo a partir do `.env`. Senhas colocadas nas
URLs não podem conter `@`, `:`, `/` ou `#`, nem quebra de linha.

```bash
docker compose up -d --no-deps --force-recreate pgbouncer worker-qualification worker-ia dashboard-ui celery-beat
```

### Juiz de 2%

`MODELO_JUIZ` vazio, ou igual ao modelo da extração, deixa a rotina em `sem_juiz` e não chama
a API. Para ligar a auditoria, escolha outro modelo, rápido e barato, com raciocínio
analítico — um endpoint ou uma versão distinta do extrator. A mesma `GEMINI_API_KEY` do
`worker-ia` precisa de cota para esse modelo. O Beat só enfileira a cada cinco minutos; a
chamada roda na `ai_multimodal_queue`. Um HTTP 429 interrompe o lote, apaga as amostras
ainda sem parecer e não reagenda a tarefa, para o erro de cota não ocupar a fila da análise.

### Papel `lifecycle_writer` em um volume já existente

A partir da migração `009`, o `pipeline_writer` perde `DELETE` em `ofertas`; só o
`lifecycle_writer` (usado pelo `celery-beat`) pode apagar linhas. Em um volume criado antes
dessa mudança, use a ordem de "Atualizar um volume já existente": recriar o `postgres` só
monta o `zz-lifecycle-role.sh` (ele não roda sozinho), a execução manual cria o papel, o
`apply-migrations.sh` aplica a `009` e o `docker compose up -d --build` recria o
`celery-beat` já com `lifecycle_writer`. Se a atualização atravessar as 03:00, a expiração
daquela noite pode falhar sem apagar nada e roda de novo na noite seguinte.

`GEMINI_API_KEY` e as chaves do R2 podem ficar no `.env`. Para limitar o acesso por serviço,
um arquivo não vazio em `secrets/gemini/GEMINI_API_KEY` substitui a variável para o worker de
IA e o dashboard; arquivos homônimos em `secrets/r2/` substituem as variáveis para a
qualificação e o lifecycle. Arquivo ausente ou vazio mantém o `.env`. Crie os diretórios e
copie os valores antes de recriar os serviços, sem `down -v`:

```bash
mkdir -p secrets/gemini secrets/r2
printf '%s' "$GEMINI_API_KEY" > secrets/gemini/GEMINI_API_KEY
printf '%s' "$CLOUDFLARE_R2_SECRET_KEY" > secrets/r2/CLOUDFLARE_R2_SECRET_KEY
docker compose up -d --no-deps --force-recreate worker-qualification worker-ia celery-beat dashboard-ui
```

A referência persistida é a chave `videos/<id>.mp4`. A URL de playback nasce no diálogo
e morre em 300 s. `CLOUDFLARE_R2_PUBLIC_URL` não entra mais na coluna. Objetos antigos
gravados como URL pública continuam legíveis: a chave é reconhecida dentro da URL e
assinada na leitura. O par `CLOUDFLARE_R2_ACCESS_KEY_PROXIMO` /
`CLOUDFLARE_R2_SECRET_KEY_PROXIMO` fica disponível para rotação; a assinatura usa o par
vigente. `infra/r2-deny-listbucket-anonimo.json` é só referência e não é aplicado na Cloudflare.

O painel e o Flower continuam em `127.0.0.1:8501` e `127.0.0.1:5555`. `infra/Caddyfile`
é uma camada opcional na frente desses binds, com os cabeçalhos de segurança. Ele não
sobe no `docker compose up`. O dashboard assina o vídeo com a secret key que o compose
já injeta em `dashboard-ui`; o volume `/run/secrets` desse serviço continua sendo o do Gemini.

## Busca semântica e paginação

Após entrar no dashboard, use a busca para descrever um nicho, promessa, gancho ou
necessidade. Cada envio de uma frase gera um embedding de consulta com
`gemini-embedding-2`; o botão **Carregar mais** reutiliza esse vetor e não chama o modelo
novamente. A listagem e a busca carregam dez ofertas por página, usando cursor em vez de
`OFFSET`.

Em um volume PostgreSQL já existente, aplique as migrações pendentes com:

```bash
./infra/apply-migrations.sh
```

O comando preserva `postgres_data`. Ele registra as versões novas e, se a migração `003`
ainda não tiver sido gravada, corrige status, score, margem e preço inválidos antes de criar
as constraints. A migração `006` concede `DELETE` em `ofertas` ao papel `pipeline_writer`; a
migração `009` revoga esse `DELETE`, pois a partir dela só o `lifecycle_writer` apaga linhas
(veja "Papel `lifecycle_writer` em um volume já existente" acima). A migração `019` acrescenta COGS, fornecedor e provas sociais; o detalhe está em "Preço da página, grupo e fornecedor".

## Ofertas com o mesmo vídeo

Uma oferta cujo vídeo (mesma URL no R2 ou mesmo hash do criativo) já foi analisado herda a
análise da irmã sem chamar o Gemini: transcrição, gancho, nicho, gatilho, objeções, ângulo e
embedding. Preço e margem ficam vazios, porque a irmã aponta para outra página de destino.
Quando a IA conclui uma oferta, as irmãs ainda pendentes recebem a análise na mesma
transação.

Ofertas herdadas antes dessa regra podem exibir o preço de outra loja. A consulta abaixo é
opcional, altera dados de produção e não roda automaticamente: ela limpa preço e margem das
concluídas que compartilham o texto analisado com uma irmã concluída mais antiga.

```bash
docker compose exec -T postgres psql -U spy_admin -d mineracao_fb -c "
UPDATE ofertas AS copia
SET preco_oferta_estimado = NULL, margem_estimada_pct = NULL
WHERE copia.status_qualificacao = 'concluido'
  AND (copia.preco_oferta_estimado IS NOT NULL OR copia.margem_estimada_pct IS NOT NULL)
  AND EXISTS (
    SELECT 1 FROM ofertas AS origem
    WHERE origem.id < copia.id
      AND origem.status_qualificacao = 'concluido'
      AND origem.embedding_texto_hash = copia.embedding_texto_hash
      AND (origem.url_video_r2 = copia.url_video_r2 OR origem.phash_criativo = copia.phash_criativo)
  );"
```

## Preço da página, grupo e fornecedor

A página de destino desta oferta é a fonte do preço de venda. A leitura aceita BRL ou USD e descarta valor ambíguo. O preço da irmã não é copiado.

Criativos do mesmo produto formam um grupo. A oferta mais antiga é a canônica (`oferta_canonica_id` nulo) e as outras apontam para ela. A triagem do grupo fica na canônica.

A revisão que roda a cada dois minutos também busca o fornecedor de até cinco canônicas que já tiveram o preço consultado. A imagem do criativo, ou a `og:image` da loja, vai para a busca pública do 1688 (CNY) e do AliExpress (USD). O 1688 prevalece. A página escolhida contribui com até cinco depoimentos de nota 5. A margem do modelo só vira cálculo quando a moeda da venda é a mesma do COGS. O contrato dessas etapas está em `docs/SKILLS_IA.md` e `docs/SDD.md`.

Em um volume que já existe, a migração `019_cogs_fornecedor_e_provas` cria essas colunas. Rode `./infra/apply-migrations.sh` antes de subir o worker novo.

## Expiração de ofertas fracas

Todos os dias, às 03:00, o contêiner `celery-beat` remove ofertas com score abaixo de 40
cuja última captura tem mais de 30 dias. O vídeo exclusivo é apagado do R2 antes da linha.
Essa exclusão de mídia é irreversível. Um arquivo ainda referenciado por outra oferta é
preservado. A linha é relida sob lock antes de expirar, portanto uma recaptura concorrente
não remove uma oferta que deixou de atender aos critérios. Se a remoção no R2 falhar, a linha
permanece para a tentativa seguinte.



## 🛠️ Comandos Úteis e Diagnóstico (Day-1 e Day-2)

* **Acessar Interfaces Locais:**
* **Dashboard Analítico:** [http://localhost:8501](http://localhost:8501)
* **Monitoramento Celery (Flower):** [http://localhost:5555](http://localhost:5555)


* **Verificar Logs do Extrator:**
```bash
docker compose logs -f ad-producer

```


* **Verificar filas do Celery no terminal:**
```bash
docker compose logs worker-ia worker-qualification | grep -E "boot steps|ready"

```


* **Auditoria da memória do Redis (Prevenção de OOM):**
```bash
docker compose exec redis redis-cli -a <SUA_SENHA_REDIS> info memory | grep -E "maxmemory_human|maxmemory_policy"

```


* **Verificar banco de dados e índice Vetorial:**
```bash
docker compose exec postgres psql -U spy_admin -d mineracao_fb -c "\d ofertas"

```


* **Backup Manual do Banco (com pgvector):**
```bash
docker compose exec postgres pg_dump -U spy_admin -d mineracao_fb -F c -b -v -f /var/lib/postgresql/data/backup_mineracao.dump

```



## Sessão do navegador

O `ad-producer` entra numa única aba que o operador já abriu em `https://www.facebook.com/ads/library`, ou na URL de `CDP_TARGET_URL`. O código não abre perfil de anti-detect, não aquece conta e não simula `Add to Cart`. Se a aba cair, o producer para. A pausa explícita com alerta, e o restante das fronteiras de margem, funil e backup, estão em `docs/FRONTEIRAS.md`. Nada daquele documento está implementado além do que `docs/SDD.md` lista na seção da 0.8.1.
