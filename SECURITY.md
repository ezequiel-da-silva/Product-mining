# Política de Segurança

Valorizamos a segurança da nossa infraestrutura e dos dados que processamos.

## Reportar uma Vulnerabilidade

Se descobrir uma vulnerabilidade de segurança neste projeto, por favor, **NÃO** crie uma *issue* pública.

Envie um email para [seu-email@dominio.com] com:
1. O tipo de problema (ex: exposição de chave API, injeção SQL no RAG).
2. Passos para reproduzir o problema.
3. Potencial impacto.

A nossa equipa irá responder num prazo de 48 horas e trabalhar para resolver o problema de forma privada antes da sua divulgação.

## Práticas de Segurança Ativas
- Utilizamos o `Bandit` no nosso CI para análise estática de código Python.
- Bloqueamos commits que contenham chaves privadas usando hooks pré-commit.
- O acesso ao banco de dados pelo frontend (Streamlit) é estritamente limitado à leitura (`SELECT`).
