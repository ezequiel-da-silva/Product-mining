## Tipo de Alteração

- [ ] `feat` — nova funcionalidade
- [ ] `fix` — correção de bug
- [ ] `perf` — melhoria de performance ou custo (FinOps)
- [ ] `refactor` — mudança interna sem alterar comportamento externo
- [ ] `chore` — manutenção, build, dependências ou CI
- [ ] `security` — hardening ou correção de segurança

## Contexto & Motivação

<!-- Descreva brevemente o problema que motivou a alteração e a solução adotada. -->

## FinOps & Impacto de IA

- [ ] Esta alteração afeta o consumo de tokens do Gemini, as quotas do Cloudflare R2 ou os parâmetros de extração (ex.: janela de vídeo, `thinking_budget`, dimensão de embedding).

Se marcado, descreva o impacto esperado (custo, volume de chamadas, tamanho de payload):

<!-- ... -->

## Migrações & Banco de Dados

- [ ] Esta alteração inclui uma nova migração SQL idempotente em `infra/migrations/`, registrada em `infra/init-db.sql` e em `infra/apply-migrations.sh`.

## Checklist de Qualidade Local

- [ ] `pytest -q` (todos os testes verdes)
- [ ] `ruff check .` e `ruff format --check .`
- [ ] `docker compose config --quiet`
- [ ] Permissões de roles e segredos `/run/secrets` preservados (nenhum serviço recebeu credenciais que não usa)
