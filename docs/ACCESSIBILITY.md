# Diretrizes de Acessibilidade (Frontend Streamlit)

O dashboard é uma página interna do Streamlit. O foco e o teclado pertencem aos widgets do framework. O layout é de cards, não de tabela.

## Paleta

O tema em `.streamlit/config.toml` é escuro:

- fundo `#0E1117`
- superfície dos cards `#1A1D27`
- texto `#FAFAFA`
- destaque `#5B46C7` (texto claro sobre o botão primário)
- link sobre o card `#B9A8FF`
- badge de margem com texto `#0E1117` sobre `#9AE6B4` (alta), `#F6E05E` (média), `#FEB2B2` (baixa) e `#E2E8F0` (estimativa)

Texto claro sobre fundo escuro. Estado de margem, triagem e aviso de HTTP também aparecem por escrito, sem depender só de cor.

## Cards

Cada oferta concluída é um card com:

- nicho
- preço, ou "Dados comerciais ausentes"
- margem em texto (alta, média, baixa ou não identificada). Quando a venda e o COGS estão na mesma moeda, o badge é **COGS 1688** ou **COGS AliExpress**. Nos outros casos o badge continua **Estimativa Sourcing (China)**, com a legenda de que o número é markup padrão, não conciliação fiscal, e o valor do COGS aparece ao lado quando existe
- a contagem de depoimentos de 5 estrelas, quando a oferta tem prova social
- pontuação em escala de 100
- data da última captura
- gancho curto
- link "Abrir loja" quando há URL http ou https
- botão de detalhes, Salvar, Descartar e Testando
- caixa **Selecionar a oferta {nicho} #{id}**

O botão de detalhes usa o texto `Ver detalhes da oferta {nicho} #{id}`. O Streamlit lê esse texto como nome do botão, então ofertas do mesmo nicho não ficam com o mesmo rótulo.

Com o foco dentro do card, S salva, D descarta e T marca testando. O atalho não dispara enquanto o foco está num campo de texto. Com o diálogo aberto, Tab circula só dentro dele e S, D e T não alcançam o card de trás. A seleção de vários cards grava o mesmo estado em `triagem_oferta` numa única transação, pelos botões Salvar seleção, Descartar seleção e Testando seleção.

## Anúncios

Uma região `role="status"` com `aria-live="polite"` informa a contagem de ofertas carregadas e, quando a cota de 12 embeddings por minuto bloqueia a busca, o texto desse bloqueio.

## Diálogo

O título do diálogo é o mesmo texto do botão que o abriu (`Ver detalhes da oferta {nicho} #{id}`).

- A prévia do vídeo tem a legenda "Prévia do vídeo do anúncio", controles nativos e botões nomeados 1,5×, 2× e avançar 1 quadro.
- A transcrição do Gemini fica no expander **Transcrição do Criativo**, logo abaixo do player.
- O gancho, a copy, as objeções e os ângulos permanecem no mesmo diálogo.
- A loja abre em nova aba com `rel="noopener noreferrer"`.
- Destino `http://` mostra o aviso de tráfego sem criptografia em domínio externo.
- O bloco **Fornecedor** mostra o COGS em texto, cada depoimento em texto e a foto do cliente com `referrerpolicy="no-referrer"` e o texto alternativo "Foto enviada pelo cliente". O link **Abrir fornecedor** usa o mesmo `rel="noopener noreferrer"`.

## Texto

O título do painel não usa emoji decorativo.
