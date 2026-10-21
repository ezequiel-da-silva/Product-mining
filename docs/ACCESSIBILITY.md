# Diretrizes de Acessibilidade (Frontend Streamlit)

O dashboard é uma página interna do Streamlit. O foco e o teclado pertencem aos widgets do framework. O conteúdo próprio do projeto segue estas regras:

1. **Contraste**: `.streamlit/config.toml` usa fundo preto, texto branco e destaque amarelo.
2. **Tabela**: as colunas têm rótulos em português. O preço é texto em reais (`R$ 1.234,50`). Margem e data usam formatação explícita, e a tabela tem uma legenda que descreve a ordenação. A seção usa um heading, sem marcação Markdown.
3. **Texto**: o título não usa emoji decorativo, para não poluir a leitura por leitores de tela.
4. **Imagens**: quando houver preview de anúncio, a imagem deve ter legenda descritiva (`st.image(..., caption="Prévia do anúncio")`).
