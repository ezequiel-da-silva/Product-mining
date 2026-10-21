# Guia de Contribuição

Obrigado por considerar contribuir para o Minerador de Produtos Avançado!

## Padrão de Commits

Utilizamos [Conventional Commits](https://www.conventionalcommits.org/). O formato de cada commit deve ser:
`<tipo>: <descrição curta>`

Tipos permitidos:
- `feat:` Nova funcionalidade.
- `fix:` Resolução de um bug.
- `docs:` Alterações apenas na documentação.
- `refactor:` Mudança no código que não corrige um bug nem adiciona uma funcionalidade.
- `chore:` Atualizações de tarefas de build, gestores de pacotes, etc.

## Processo de Pull Request

1. Crie uma branch para a sua funcionalidade (`git checkout -b feat/nome-da-funcionalidade`).
2. Garanta que o seu código passa nos hooks de pre-commit (`pre-commit run --all-files`).
3. Faça push da sua branch e abra o Pull Request.
4. O CI irá correr testes de segurança (Trivy/Bandit). Apenas PRs com sucesso serão aprovados.
