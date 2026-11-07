# Como fazer a classificação

Guia para quem vai classificar as notícias do Entrelinhas. Você não precisa
coletar nada: recebe um banco pronto, classifica e devolve.

**Você precisa de:**
- Python 3.12 instalado ([python.org](https://www.python.org/downloads/)).
- Os arquivos do projeto (repositório do GitHub ou zip enviado pelo grupo).
- O `pacote_classificacao.zip` (enviado pelo grupo por Drive; não está no GitHub).
- Uma chave de API do Jev, em [console.typesafe.ai](https://console.typesafe.ai), com crédito.

São cerca de 3.050 notícias (os acontecimentos que aparecem no site).

## 1. Montar a pasta

1. Crie uma pasta, por exemplo `entrelinhas`, e coloque nela os arquivos do projeto.
2. Descompacte o `pacote_classificacao.zip` **dentro dessa mesma pasta**. Devem
   aparecer `noticias.db` e `capas.json` ao lado dos arquivos `.py`.
3. Abra o terminal nessa pasta (no VS Code: Terminal > New Terminal).

## 2. Instalar (uma vez)

Windows (PowerShell):
```
py -V:3.12 -m venv .venv
.venv\Scripts\activate
pip install typesafe-sdk
```

Mac (Terminal):
```
python3.12 -m venv .venv
source .venv/bin/activate
pip install typesafe-sdk
```

Toda vez que abrir o terminal de novo, ative o ambiente (a segunda linha acima)
antes dos comandos. Quando estiver ativo, aparece `(.venv)` no começo da linha.

> Nos comandos abaixo, use `python` no Windows e `python3` no Mac.

## 3. Guardar a chave do Jev (uma vez)

```
python classificar_noticias.py --salvar-chave
```

Cole a chave e aperte Enter (ela não aparece na tela). Ela fica no arquivo
`.typesafe_key`. **Não mande esse arquivo para ninguém nem para o GitHub, e
não cole a chave em conversas.**

## 4. Conferir quantas faltam (não gasta crédito)

```
python classificar_noticias.py --estimativa
```

## 5. Teste com 30 notícias

```
python classificar_noticias.py --limite 30
python classificar_noticias.py --exemplos 10
```

Leia os exemplos e veja se fazem sentido. Confira no painel do Jev quanto o
teste gastou, para estimar o total.

## 6. Classificar tudo

```
python classificar_noticias.py --log classificacao.log
```

Pode interromper (Ctrl+C) e rodar de novo: ele continua de onde parou e
refaz só as que deram erro. No fim aparece "Tudo classificado".

## 7. (Opcional) Validação à mão

Para medir o acerto do modelo:
```
python classificar_noticias.py --validacao 200
```
Gera `validacao.csv`. Preencha as colunas de anotação humana e depois:
```
python classificar_noticias.py --avaliar validacao.csv
```

## 8. Gerar os dados do site

```
python classificar_noticias.py --stats
python exportar_site.py
```

O primeiro mostra a distribuição por portal. O segundo gera `dados_site.json`.

**Tema dos acontecimentos.** Por padrão o site continua com o tema definido
pelo vocabulário (já conferido). Só se o grupo decidir usar o tema escolhido
pelo Jev, rode antes do `exportar_site.py`:
```
python classificar_noticias.py --temas-grupos
```

## 9. Devolver

Mande para o grupo:
1. `dados_site.json` (para atualizar o site);
2. o `noticias.db` compactado em zip, com o nome `classificado.zip` (para o grupo
   guardar as classificações no banco completo).

Quem tem o banco completo roda:
```
python preparar_pacote.py --importar classificado.zip
```

## Os critérios

| Dimensão | Rótulos | Escopo de Leitura |
|---|---|---|
| Tipo de conteúdo | informativa, analítica, opinativa | título + texto |
| Tom da manchete | negativo, neutro, positivo | só o título |
| Profundidade | superficial, intermediária, aprofundada | título + texto |
| Postura | a favor, neutra, contra | título + texto |
| Viés político | esquerda, neutro, direita | título + texto |
| Tema principal | os 8 temas da coleta | título (+ texto se houver) |

> **Nota Metodológica (Texto Curto / Vídeo):** Notícias com menos de 80 palavras (ex.: matérias em formato de vídeo ou enquetes) são marcadas com `texto_insuficiente = 1`. Nessas notícias, o modelo classifica estritamente o **Tom da Manchete** e o **Tema Principal** a partir do título. As 4 dimensões que dependem de corpo de texto (`tipo`, `profundidade`, `postura` e `vies_politico`) ficam nulas (`NULL`).

As definições de cada rótulo estão em `DIMENSOES`, no começo de
`classificar_noticias.py`. Mudar o texto lá muda a classificação; combine com o
grupo antes.

## Problemas comuns

- **"Chave não encontrada" ou "Chave inválida"**: rode o passo 3 de novo.
- **"Nenhum texto baixado"** ou **"no such table"**: o `noticias.db` não está na
  pasta certa. Ele precisa estar ao lado dos arquivos `.py`.
- **`py` não é reconhecido** (Windows): use `python` no lugar de `py -V:3.12`.
- **Muitos erros de limite**: rode com menos requisições ao mesmo tempo,
  `python classificar_noticias.py --paralelo 2`.
