# Entrelinhas

Plataforma de comparação de notícias (FGV Comunicação). Coleta notícias de 5
portais brasileiros, agrupa as que falam do mesmo acontecimento, classifica
cada uma e mostra lado a lado como cada portal deu a notícia.

Portais: G1, CNN Brasil, Metrópoles, Poder360 e Agência Brasil.
Temas: política, economia, saúde, educação, meio ambiente, esporte e cultura.
Período: 3 meses (03/08/2026 a 02/11/2026).

**Quem vai classificar: siga o [CLASSIFICACAO.md](CLASSIFICACAO.md).**

## Instalação (Windows, Python 3.12)

```
py -V:3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

No Mac: `python3.12 -m venv .venv` e `source .venv/bin/activate`.

O banco `noticias.db` não fica no repositório: é grande e guarda os textos
completos das matérias. Peça a cópia ao grupo.

## Etapas

| # | Comando | O que faz |
|---|---|---|
| 1 | `python coletor_noticias.py --log historico.log` | Coleta 90 dias pelo RSS do Google News, por portal, dia e tema |
| 2 | `python agrupar_noticias.py` | Agrupa notícias do mesmo acontecimento pelo título (janela de 48 h) |
| 3 | `python coletor_noticias.py --decode --log decode.log` | Resolve o link original das notícias agrupadas |
| 4 | `python baixar_textos.py --log textos.log` | Baixa o texto, a foto e marca colunas de opinião |
| 5 | `python padronizar_base.py --limpar` | Tira PDFs, páginas automáticas, duplicatas e textos curtos |
| 6 | `python definir_temas.py` | Dá um tema a cada acontecimento pelo vocabulário dos títulos |
| 7 | `python baixar_capas.py` | Escolhe a foto de melhor qualidade de cada acontecimento (`capas.zip`) |
| 8 | `python classificar_noticias.py` | Classifica tipo, tom da manchete, profundidade e postura (Jev) |
| 9 | `python exportar_site.py` | Gera `dados_site.json`, que alimenta o site |

As etapas retomam de onde pararam se forem interrompidas.

### Classificação em outro computador

```
python preparar_pacote.py                              # gera pacote_classificacao.zip
python preparar_pacote.py --importar classificado.zip  # traz o resultado de volta
```

## Conferências

```
python verificar_pasta.py                 # arquivos, bibliotecas e banco
python agrupar_noticias.py --amostra 40   # amostra de grupos para validar à mão
python definir_temas.py --amostra 30      # amostra de temas
python baixar_textos.py --stats           # taxa de sucesso por portal
python classificar_noticias.py --stats    # distribuição dos rótulos por portal
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
- Notícias com menos de 80 palavras recebem só o tom da manchete.
- Os textos completos ficam só no banco local, para análise. O site mostra
  apenas título, um trecho de ~200 caracteres e o link para o portal.
