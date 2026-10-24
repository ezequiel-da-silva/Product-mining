# Minerador de Produtos Avançado (Facebook Ads) 🚀

Este projeto é uma arquitetura de nível Enterprise para extração, qualificação, enriquecimento e armazenamento de anúncios validados do Facebook Ads. Utiliza microsserviços, inteligência artificial multimodal (Gemini 2.5 Flash), fila de tarefas assíncronas e busca vetorial (HNSW) para criar um SaaS autônomo e resiliente.

## 🏗️ Arquitetura e Stack Tecnológico

- **Producer (Extrator):** Python + Playwright conectado via CDP a navegadores Anti-Detect. Interceptação direta de tráfego GraphQL (Zero Fingerprint) e mascaramento de automação (`playwright-stealth`).
- **Message Broker:** Redis 7 + Celery para roteamento estrito e concorrência balanceada. Monitoramento via Flower.
- **Storage Relacional e Vetorial:** PostgreSQL 16 com extensão `pgvector` e busca otimizada HNSW.
- **Workers:** Segregação entre qualificação de rotas (APIs) e Worker Multimodal (Gemini AI + Jinja2 para templates de prompts).
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
├── docs/                      # Documentação de Arquitetura (SDD), Acessibilidade e Avaliação de Agentes
├── infra/
│   └── init-db.sql            # DDL do PostgreSQL, índices pgvector e RBAC
├── prompts/                   # Templates e versionamento de instruções da IA (LLMOps)
├── src/
│   ├── producer.py            # Script principal de interceptação via Playwright
│   ├── worker_ia.py           # Worker que consome o Gemini e Cloudflare R2
│   └── dashboard.py           # Frontend em Streamlit
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

Atualize `PIPELINE_DB_PASSWORD`, `LIFECYCLE_DB_PASSWORD` e `STREAMLIT_DB_PASSWORD` no `.env`
com os mesmos valores. Senhas colocadas nessas URLs não podem conter `@`, `:`, `/` ou `#`.
Recrie só quem usa essas URLs:

```bash
docker compose up -d --no-deps --force-recreate worker-qualification worker-ia dashboard-ui celery-beat
```

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

Configure `CLOUDFLARE_R2_PUBLIC_URL` para obter URLs permanentes dos vídeos. Sem ela,
o worker usa uma URL pré-assinada válida por até sete dias.

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
(veja "Papel `lifecycle_writer` em um volume já existente" acima).

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



## 🛡️ Protocolo de Farming (Contas Isca)

Para evitar bloqueios algorítmicos rigorosos, o sistema requer aquecimento (warm-up) das contas na máquina hospedeira antes de automatizar 100%:

* **D1 a D3:** Cadastro sob IP móvel (4G/5G), navegação orgânica passiva e isolamento de hardware via perfil no Anti-Detect (5-10 min/dia).
* **D4 e D5:** Aquecimento de Pixel comercial (buscas por nicho, simulação de *Add to Cart* e abandono de checkout em lojas Shopify/D2C).
* **D7 em diante:** Ativação do script Playwright (contêiner `ad-producer`) em sessões de no máximo 15 minutos e rotação cíclica entre 3 perfis isca segmentados por nicho.
