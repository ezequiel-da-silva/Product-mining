# Critérios de Avaliação do Agente de IA

O Worker de IA extrai métricas importantes (Nicho, Objeções, Preço).

## Prevenção de Alucinação
1. **Margem de Erro de Preço**: O agente só deve preencher `preco_oferta_estimado` se houver menção explícita na transcrição ou na copy. Caso contrário, deve retornar nulo.
2. **Avaliação Humana Aleatória**: Semanalmente, 5 vídeos pontuados com "Score >= 80" devem ser revistos manualmente para calibrar as instruções do `system_persona.yaml`.
