# Entrelinhas

Plataforma de comparação de notícias (FGV Comunicação). Coleta notícias de 5
portais brasileiros, agrupa as que falam do mesmo acontecimento, classifica
cada uma e mostra lado a lado como cada portal deu a notícia.

Portais: G1, CNN Brasil, Metrópoles, Poder360 e Agência Brasil.
Temas: política, economia, saúde, educação, meio ambiente, esporte e cultura.
Histórico desde 03/08/2026, atualizado todo dia.

**Para colocar no ar pela primeira vez, siga o [CONFIGURAR.md](CONFIGURAR.md).**

## Como funciona

Todo dia às 6h (Brasília), o GitHub Actions roda
`.github/workflows/atualizacao_diaria.yml`:

1. baixa o banco (`noticias.db` + `embeddings.npz`) do release **dados**;
2. roda as etapas abaixo, que só processam o que é novo;
3. salva o banco de volta no release **dados**;
4. envia a pasta `site/` atualizada para o repositório, e o Vercel republica o site.

| # | Comando | O que faz |
|---|---|---|
| 1 | `python coletor_noticias.py --atualizar` | Coleta as notícias novas pelo RSS do Google News, por portal, dia e tema |
| 2 | `python agrupar_noticias.py` | Agrupa notícias do mesmo acontecimento pelo título (janela de 48 h) |
| 3 | `python coletor_noticias.py --decode` | Resolve o link original das notícias agrupadas |
| 4 | `python baixar_textos.py` | Baixa o texto, a foto e marca colunas de opinião |
| 5 | `python padronizar_base.py --limpar --ate-hoje --sem-backup` | Tira PDFs, páginas automáticas, duplicatas e textos curtos |
| 6 | `python agrupar_noticias.py` | Refaz os grupos depois da limpeza |
| 7 | `python definir_temas.py` | Dá um tema a cada acontecimento pelo vocabulário dos títulos |
| 8 | `python classificar_noticias.py` | Classifica com o Gemini (tipo, tom, profundidade, postura, viés político, tema) |
| 9 | `python exportar_site.py` | Gera `site/dados_site.json` |
| 10 | `python baixar_capas.py` | Baixa a capa dos acontecimentos novos para `site/capas/` |
| 11 | `python montar_site.py` | Junta modelo, dados e capas em `site/index.html` |

Para rodar na hora, sem esperar o horário: aba **Actions** > **Atualização
diária** > **Run workflow**.

## Rodar no próprio computador (opcional)

```
py -V:3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Baixe o `banco.zip` do release **dados** e descompacte na pasta. A chave do
Gemini vai em `python classificar_noticias.py --salvar-chave` (fica em
`.gemini_key`, que nunca vai para o GitHub). Se você mudar o banco no seu
computador, suba o novo `banco.zip` no release, senão a próxima rodada
automática parte da versão antiga.

## Conferências

```
python verificar_pasta.py                 # arquivos, bibliotecas e banco
python agrupar_noticias.py --amostra 40   # amostra de grupos para validar à mão
python definir_temas.py --amostra 30      # amostra de temas
python classificar_noticias.py --stats    # distribuição dos rótulos por portal
python classificar_noticias.py --exemplos 10      # classificações com justificativa
python classificar_noticias.py --validacao 200    # planilha para anotar à mão
python classificar_noticias.py --avaliar validacao.csv   # acerto do modelo
```

## Decisões de metodologia

- A coleta divide cada dia em buscas por tema, porque o RSS devolve no máximo
  ~100 itens por busca. A base é uma amostra grande de cada portal, não a
  cobertura completa.
- Agrupamento: semelhança mínima de 0,80 entre títulos (com o centro do grupo
  e com a notícia que o fundou), no máximo 3 notícias de um portal por grupo,
  títulos com menos de 4 palavras ignorados.
- O site mostra os acontecimentos cobertos por 3 ou mais portais.
- Cada acontecimento tem um único tema, definido pelo vocabulário dos títulos
  e do começo dos textos.
- A classificação usa o Gemini com resposta presa aos rótulos de cada
  critério. A confiança de cada critério é a avaliação do próprio modelo (não
  uma probabilidade medida); abaixo de 60% a notícia não entra nas colunas.
  O viés político é um critério separado da postura.
- Notícias com menos de 80 palavras recebem só o tom da manchete e o tema.
- Os textos completos ficam só no banco (release privado). O site mostra
  apenas título, um trecho de ~200 caracteres e o link para o portal.
