# Diretrizes de Acessibilidade (Frontend Streamlit)

O dashboard é uma página interna do Streamlit. O foco e o teclado pertencem aos widgets do framework. O layout é de cards, não de tabela.

## Paleta

O tema em `.streamlit/config.toml` é escuro:

- fundo `#0E1117`
- superfície dos cards `#1A1D27`
- texto `#FAFAFA`
- destaque `#7C6AF7`

Texto claro sobre fundo escuro. Estado de margem, triagem e aviso de HTTP também aparecem por escrito, sem depender só de cor.

## Cards

Cada oferta concluída é um card com:

- nicho
- preço, ou "Dados comerciais ausentes"
- margem em texto (alta, média, baixa ou não identificada), o badge **Estimativa Sourcing (China)** e a legenda de que o número é markup padrão, não conciliação fiscal
- pontuação em escala de 100
- data da última captura
- gancho curto
- link "Abrir loja" quando há URL http ou https
- botão de detalhes, Salvar, Descartar e Testando

O botão de detalhes usa o texto `Ver detalhes da oferta {nicho} #{id}`. O Streamlit lê esse texto como nome do botão, então ofertas do mesmo nicho não ficam com o mesmo rótulo.

## Diálogo

- A prévia do vídeo tem a legenda "Prévia do vídeo do anúncio".
- A transcrição do Gemini fica no expander **Transcrição do Criativo**, logo abaixo do player.
- O gancho, a copy, as objeções e os ângulos permanecem no mesmo diálogo.
- A loja abre em nova aba com `rel="noopener noreferrer"`.
- Destino `http://` mostra o aviso de tráfego sem criptografia em domínio externo.

## Texto

O título do painel não usa emoji decorativo.
