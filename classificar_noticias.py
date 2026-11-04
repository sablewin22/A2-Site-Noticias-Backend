#!/usr/bin/env python3
"""
Classifica as notícias com o Google Gemini. Lê e grava no mesmo noticias.db.

Critérios (perguntados de uma vez, com resposta presa aos rótulos definidos):
- Tipo de conteúdo: informativa / analitica / opinativa          (título + texto)
- Tom da manchete:  negativo / neutro / positivo                 (só o título)
- Profundidade:     superficial / intermediaria / aprofundada    (título + texto)
- Postura:          a_favor / neutra / contra                    (título + texto)
- Viés político:    esquerda / neutro / direita                  (título + texto)
- Tema principal:   um dos 7 temas da coleta                     (título + texto)

Para cada critério o modelo também informa uma confiança de 0 a 1 (é a própria
avaliação do modelo, não uma probabilidade medida) e uma justificativa de uma
frase. Confiança abaixo de 0,5 marca a notícia para revisão.

Notícias com texto curto (menos de 80 palavras, ex.: matérias em vídeo)
recebem só o tom da manchete e o tema; o resto fica vazio e a notícia é
marcada como texto_insuficiente.

Instalação:
    pip install requests

Chave de API (crie em aistudio.google.com/apikey). Guarde num arquivo, uma vez só:
    python classificar_noticias.py --salvar-chave
(ou defina a variável de ambiente GEMINI_API_KEY)

Por padrão classifica só as notícias dos acontecimentos com 3+ portais (os que
aparecem no site). Use --min-portais 2 para incluir também os de 2 portais.

O tema de cada acontecimento continua sendo o do vocabulário (definir_temas.py).
O tema principal que o Gemini escolhe fica guardado; para usá-lo no lugar do
vocabulário, rode --temas-grupos depois de classificar tudo.

Uso:
    python classificar_noticias.py --estimativa      # quantas faltam, sem chamar o Gemini
    python classificar_noticias.py --limite 30       # teste
    python classificar_noticias.py                   # todas as pendentes
    python classificar_noticias.py --stats           # distribuição por portal
    python classificar_noticias.py --exemplos 10     # mostra 10 classificações
    python classificar_noticias.py --validacao 200   # planilha para anotar à mão
    python classificar_noticias.py --avaliar validacao.csv   # mede o acerto
"""

import argparse
import csv
import getpass
import json
import logging
import os
import random
import sqlite3
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    import requests
except ImportError:
    raise SystemExit("Instale com: pip install requests")

ARQ_CHAVE = ".gemini_key"
MODELO = os.environ.get("GEMINI_MODELO", "gemini-3.5-flash-lite")
MAX_CARACTERES = 6000      # ~1.500 tokens de texto por notícia
MIN_PALAVRAS = 80          # abaixo disso: só o tom da manchete
CONFIANCA_BAIXA = 0.5      # abaixo disso a classificação é marcada para revisão
TRABALHADORES = 4          # requisições em paralelo

# ------------------------------------------------------------- perguntas
# As descrições são as definições do projeto. Mudar aqui muda a classificação.
DIMENSOES = {
    "tipo": {
        "nome": "Tipo de conteúdo",
        "instrucoes": ("Considerando a manchete e o texto, qual é o tipo de conteúdo "
                       "desta notícia brasileira?"),
        "criterios": {
            "informativa": "Relata o que aconteceu, com fatos e declarações.",
            "analitica": "Explica causas e contexto, com dados ou especialistas.",
            "opinativa": "Defende um ponto de vista do autor ou do veículo.",
        },
        "precisa_texto": True,
    },
    "tom_manchete": {
        "nome": "Tom da manchete",
        "instrucoes": ("Considere APENAS a manchete (campo 'manchete'), não o texto. "
                       "Qual é o tom da manchete?"),
        "criterios": {
            "negativo": "A manchete enfatiza perda, risco ou problema.",
            "neutro": "A manchete descreve sem adjetivar.",
            "positivo": "A manchete enfatiza ganho, solução ou alívio.",
        },
        "precisa_texto": False,
    },
    "profundidade": {
        "nome": "Profundidade",
        "instrucoes": "Considerando o texto da notícia, qual é a profundidade da cobertura?",
        "criterios": {
            "superficial": "Informa o fato principal, sem contexto.",
            "intermediaria": "Traz contexto e ao menos uma fonte além da oficial.",
            "aprofundada": "Várias fontes, dados, histórico e consequências.",
        },
        "precisa_texto": True,
    },
    "postura": {
        "nome": "Postura sobre o assunto",
        "instrucoes": ("Avalie a postura do texto em relação ao fato ou à decisão principal "
                       "da notícia. Se a notícia não envolve uma decisão ou não toma posição "
                       "(ex.: um acidente relatado), a resposta é neutra."),
        "criterios": {
            "a_favor": "Trata a decisão ou o fato como acertado.",
            "neutra": "Não toma posição sobre o fato.",
            "contra": "Trata a decisão ou o fato como equivocado.",
        },
        "precisa_texto": True,
    },
    "vies_politico": {
        "nome": "Viés político",
        "instrucoes": ("Considerando a manchete e o texto, qual é o viés político predominante "
                       "no enquadramento, nas fontes ouvidas e na linguagem desta notícia?"),
        "criterios": {
            "esquerda": "Enquadramento, fontes ou vocabulário alinhados a pautas e atores da esquerda.",
            "neutro": "Tratamento factual, institucional ou com contraponto equilibrado entre lados.",
            "direita": "Enquadramento, fontes ou vocabulário alinhados a pautas e atores da direita.",
        },
        "precisa_texto": True,
    },
    "tema": {
        "nome": "Tema principal",
        "instrucoes": ("Qual é o assunto PRINCIPAL desta notícia? Escolha um só, mesmo que "
                       "ela mencione outros assuntos."),
        "criterios": {
            "politica": "Governo, Congresso, STF, eleições, partidos e políticos.",
            "economia": "Inflação, juros, câmbio, emprego, impostos, empresas e mercado.",
            "saude": "Doenças, SUS, hospitais, vacinas, medicamentos e saúde pública.",
            "educacao": "Escolas, universidades, Enem, professores e políticas de ensino.",
            "meio_ambiente": "Clima, desmatamento, queimadas, poluição, enchentes e secas.",
            "esporte": "Futebol e demais esportes, atletas, campeonatos e competições.",
            "cultura": "Cinema, música, shows, festivais, livros, teatro e exposições.",
        },
        "precisa_texto": False,
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("classificar")
for barulhento in ("urllib3",):
    logging.getLogger(barulhento).setLevel(logging.WARNING)


# ------------------------------------------------------------- chave
def carregar_chave():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"].strip()
    if os.path.exists(ARQ_CHAVE):
        with open(ARQ_CHAVE, encoding="utf-8") as f:
            return f.read().strip()
    raise SystemExit("Chave não encontrada. Rode: python classificar_noticias.py --salvar-chave")


def salvar_chave():
    chave = getpass.getpass("Cole a chave do Gemini e aperte Enter (ela não aparece na tela): ")
    if not chave.strip():
        raise SystemExit("Nenhuma chave digitada.")
    with open(ARQ_CHAVE, "w", encoding="utf-8") as f:
        f.write(chave.strip())
    print(f"Chave salva em {ARQ_CHAVE}. Não envie esse arquivo para o GitHub.")


# ------------------------------------------------------------- banco
def preparar_tabela(con):
    colunas = []
    for d in DIMENSOES:
        colunas += [f"{d} TEXT", f"{d}_confianca REAL"]
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS classificacoes (
                noticia_id INTEGER PRIMARY KEY,
                {", ".join(colunas)},
                probabilidades TEXT,
                texto_insuficiente INTEGER NOT NULL DEFAULT 0,
                revisar INTEGER NOT NULL DEFAULT 0,
                modelo TEXT,
                erro TEXT,
                classificado_em TEXT
            )"""
    )
    existentes = {r[1] for r in con.execute("PRAGMA table_info(classificacoes)")}
    for d in DIMENSOES:
        if d not in existentes:
            con.execute(f"ALTER TABLE classificacoes ADD COLUMN {d} TEXT")
            con.execute(f"ALTER TABLE classificacoes ADD COLUMN {d}_confianca REAL")
    try:
        con.execute("ALTER TABLE classificacoes ADD COLUMN justificativa TEXT")
    except Exception:
        pass
    con.commit()


def pendentes(con, limite, portal, min_portais=3):
    tem_textos = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='textos'").fetchone()
    if not tem_textos:
        raise SystemExit("Nenhum texto baixado. Rode antes: python baixar_textos.py")
    sql = """
        SELECT n.id, n.portal, n.titulo, t.texto, t.n_palavras
        FROM noticias n
        JOIN textos t ON t.noticia_id = n.id
        LEFT JOIN classificacoes c ON c.noticia_id = n.id
        WHERE t.status IN ('ok', 'curto')
          AND (c.noticia_id IS NULL OR c.erro IS NOT NULL)
          AND n.id IN (SELECT ng.noticia_id FROM noticia_grupo ng
                       JOIN grupos g ON g.id = ng.grupo_id WHERE g.n_portais >= ?)
    """
    params = [min_portais]
    if portal:
        sql += " AND n.portal = ?"
        params.append(portal)
    sql += " ORDER BY n.publicado_em DESC"
    if limite:
        sql += " LIMIT ?"
        params.append(limite)
    return con.execute(sql, params).fetchall()


# ------------------------------------------------------------- Gemini
def esquema(so_manchete: bool) -> dict:
    """Formato da resposta. Cada critério só aceita os rótulos definidos (enum),
    então o Gemini não consegue responder fora da lista."""
    props, obrig = {}, []
    for chave, d in DIMENSOES.items():
        if so_manchete and d["precisa_texto"]:
            continue
        props[chave] = {"type": "STRING", "enum": list(d["criterios"])}
        props[f"{chave}_confianca"] = {"type": "NUMBER"}
        obrig += [chave, f"{chave}_confianca"]
    props["justificativa"] = {"type": "STRING"}
    obrig.append("justificativa")
    return {"type": "OBJECT", "properties": props, "required": obrig,
            "propertyOrdering": obrig}


def montar_prompt(titulo, texto, so_manchete: bool) -> str:
    partes = ["Você classifica notícias da imprensa brasileira. Responda cada critério "
              "escolhendo um dos rótulos, de acordo com as definições.\n"]
    for chave, d in DIMENSOES.items():
        if so_manchete and d["precisa_texto"]:
            continue
        partes.append(f"## {chave} ({d['nome']})\n{d['instrucoes']}")
        partes += [f"- {rotulo}: {definicao}" for rotulo, definicao in d["criterios"].items()]
        partes.append("")
    partes.append("Para cada critério, informe em <criterio>_confianca um número de 0 a 1 "
                  "indicando o quanto a escolha é clara (1 = sem dúvida). "
                  "Em justificativa, explique a classificação em uma frase.\n")
    partes.append(f"Manchete: {titulo}")
    if not so_manchete:
        partes.append(f"Texto: {texto[:MAX_CARACTERES]}")
    return "\n".join(partes)


class ErroChave(Exception):
    pass


def classificar_uma(chave_api, titulo, texto, n_palavras):
    so_manchete = (n_palavras or 0) < MIN_PALAVRAS or not texto
    corpo = {
        "contents": [{"parts": [{"text": montar_prompt(titulo, texto, so_manchete)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": esquema(so_manchete),
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent"
    # a chave vai no cabeçalho, não no link, para não aparecer em logs e mensagens de erro
    cabecalho = {"x-goog-api-key": chave_api, "Content-Type": "application/json"}

    ultimo = None
    for tentativa in range(1, 6):
        try:
            resp = requests.post(url, headers=cabecalho, json=corpo, timeout=60)
        except requests.RequestException as e:
            ultimo = f"conexão: {e}"
            time.sleep(2 * tentativa)
            continue
        if resp.status_code in (401, 403):
            raise ErroChave(resp.text[:200])
        if resp.status_code == 400 and "API key" in resp.text:
            raise ErroChave(resp.text[:200])
        if resp.status_code in (429, 500, 502, 503, 504):
            ultimo = f"HTTP {resp.status_code}"
            time.sleep(min(60, 5 * tentativa))
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            dados = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
            break
        except (KeyError, IndexError, ValueError) as e:
            ultimo = f"resposta inesperada: {e}"
            time.sleep(2)
    else:
        raise RuntimeError(f"Gemini falhou 5 vezes seguidas ({ultimo})")

    resultado = {"texto_insuficiente": 1 if so_manchete else 0, "modelo": MODELO,
                 "justificativa": str(dados.get("justificativa", ""))[:500]}
    for chave, d in DIMENSOES.items():
        if so_manchete and d["precisa_texto"]:
            continue
        rotulo = str(dados.get(chave, "")).strip()
        if rotulo not in d["criterios"]:
            raise RuntimeError(f"rótulo inválido em {chave}: {rotulo!r}")
        try:
            conf = min(1.0, max(0.0, float(dados.get(f"{chave}_confianca"))))
        except (TypeError, ValueError):
            conf = None
        resultado[chave] = rotulo
        resultado[f"{chave}_confianca"] = None if conf is None else round(conf, 4)
    resultado["revisar"] = int(any(
        (resultado.get(f"{c}_confianca") or 0) < CONFIANCA_BAIXA
        for c in DIMENSOES if c in resultado))
    return resultado


def gravar(con, noticia_id, res):
    dados = dict(res)
    dados.setdefault("erro", None)
    dados["classificado_em"] = datetime.now().isoformat(timespec="seconds")
    campos = list(dados)
    con.execute(
        f"""INSERT OR REPLACE INTO classificacoes (noticia_id, {", ".join(campos)})
            VALUES (?, {", ".join("?" * len(campos))})""",
        [noticia_id] + [dados[c] for c in campos],
    )


def executar(con, limite, portal, trabalhadores, min_portais=3):
    chave_api = carregar_chave()
    linhas = pendentes(con, limite, portal, min_portais)
    if not linhas:
        print("Nenhuma notícia pendente.")
        return
    log.info("Classificando %d notícias com o %s, %d requisições em paralelo...",
             len(linhas), MODELO, trabalhadores)

    feitos = erros = curtos = 0
    with ThreadPoolExecutor(trabalhadores) as pool:
        futuros = {pool.submit(classificar_uma, chave_api, titulo, texto, n): id_
                   for id_, _portal, titulo, texto, n in linhas}
        try:
            for futuro in as_completed(futuros):
                id_ = futuros[futuro]
                try:
                    res = futuro.result()
                    curtos += res["texto_insuficiente"]
                except ErroChave:
                    pool.shutdown(cancel_futures=True)
                    raise SystemExit("Chave do Gemini inválida ou sem permissão. Rode de novo: "
                                     "python classificar_noticias.py --salvar-chave")
                except Exception as e:
                    res = {"erro": f"{type(e).__name__}: {e}"[:300]}
                    erros += 1
                gravar(con, id_, res)
                feitos += 1
                if feitos % 25 == 0 or feitos == len(linhas):
                    con.commit()
                    log.info("[%d/%d] erros: %d | só manchete: %d",
                             feitos, len(linhas), erros, curtos)
        except KeyboardInterrupt:
            log.info("Interrompido. O que já foi classificado está salvo.")
            pool.shutdown(cancel_futures=True)
        finally:
            con.commit()

    if erros:
        exemplo = con.execute(
            "SELECT erro FROM classificacoes WHERE erro IS NOT NULL LIMIT 1").fetchone()
        log.warning("%d erros. Exemplo: %s", erros, exemplo[0] if exemplo else "?")
        log.warning("Rode o mesmo comando de novo para tentar só as que falharam.")
    restantes = len(pendentes(con, None, portal, min_portais))
    if restantes:
        log.info("Faltam %d notícias. Rode o mesmo comando para continuar.", restantes)
    else:
        log.info("Tudo classificado. Próximo passo: python exportar_site.py")


# ------------------------------------------------------------- relatórios
def tema_dos_grupos(con):
    """Um tema por acontecimento: o tema principal da maioria das notícias do
    grupo; em caso de empate, o de maior confiança somada. Devolve
    {grupo_id: (tema, votos, total)}."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='noticia_grupo'").fetchone():
        return {}
    votos = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for grupo, tema, conf in con.execute(
            """SELECT ng.grupo_id, c.tema, c.tema_confianca
               FROM noticia_grupo ng JOIN classificacoes c ON c.noticia_id = ng.noticia_id
               WHERE c.erro IS NULL AND c.tema IS NOT NULL"""):
        v = votos[grupo][tema]
        v[0] += 1
        v[1] += conf or 0
    resultado = {}
    for grupo, temas in votos.items():
        tema, (n, _) = max(temas.items(), key=lambda kv: (kv[1][0], kv[1][1]))
        resultado[grupo] = (tema, n, sum(x[0] for x in temas.values()))
    return resultado


def gravar_tema_dos_grupos(con):
    """Troca o tema do vocabulário pelo tema principal do Gemini, só nos
    acontecimentos que já têm notícias classificadas. Os outros mantêm o tema
    do vocabulário."""
    con.execute("""CREATE TABLE IF NOT EXISTS grupo_tema (
                       grupo_id INTEGER PRIMARY KEY, tema TEXT, votos INTEGER, total INTEGER)""")
    temas = tema_dos_grupos(con)
    con.executemany("INSERT OR REPLACE INTO grupo_tema (grupo_id, tema, votos, total) "
                    "VALUES (?,?,?,?)",
                    [(g, t, v, n) for g, (t, v, n) in temas.items()])
    con.commit()
    unanimes = sum(1 for _, v, n in temas.values() if v == n)
    print(f"{len(temas)} acontecimentos com tema definido "
          f"({unanimes} com todas as notícias concordando)")
    for tema, qtd in Counter(t for t, _, _ in temas.values()).most_common():
        print(f"  {tema:<16} {qtd:>6}")


def mostrar_stats(con):
    total = con.execute(
        "SELECT COUNT(*) FROM classificacoes WHERE erro IS NULL").fetchone()[0]
    if not total:
        print("Nenhuma notícia classificada ainda.")
        return
    insuf, revisar = con.execute(
        """SELECT SUM(texto_insuficiente), SUM(revisar) FROM classificacoes
           WHERE erro IS NULL""").fetchone()
    print(f"\nClassificadas: {total} | só manchete (texto curto): {insuf} | "
          f"confiança baixa (revisar): {revisar}")

    for chave, d in DIMENSOES.items():
        rotulos = list(d["criterios"])
        print(f"\n{d['nome']}")
        print(f"  {'portal':<16}" + "".join(f"{r:>15}" for r in rotulos))
        dados = defaultdict(Counter)
        for portal, valor in con.execute(
                f"""SELECT n.portal, c.{chave} FROM classificacoes c
                    JOIN noticias n ON n.id = c.noticia_id
                    WHERE c.erro IS NULL AND c.{chave} IS NOT NULL"""):
            dados[portal][valor] += 1
        for portal in sorted(dados):
            soma = sum(dados[portal].values())
            print(f"  {portal:<16}" + "".join(
                f"{100 * dados[portal][r] / soma:>14.0f}%" for r in rotulos))
    print()


def mostrar_exemplos(con, quantos):
    linhas = con.execute(
        f"""SELECT n.portal, n.titulo, c.tipo, c.tom_manchete, c.profundidade, c.postura,
                   c.tipo_confianca, c.tom_manchete_confianca,
                   c.profundidade_confianca, c.postura_confianca, c.tema, c.tema_confianca,
                   c.vies_politico, c.vies_politico_confianca, c.justificativa
            FROM classificacoes c JOIN noticias n ON n.id = c.noticia_id
            WHERE c.erro IS NULL ORDER BY RANDOM() LIMIT ?""", (quantos,)).fetchall()
    for (portal, titulo, tipo, tom, prof, post, ct, cm, cp, cs, tema, ctm,
         vies, cv, just) in linhas:
        def f(v, c):
            return f"{v} ({c:.2f})" if v else "—"
        print(f"\n[{portal}] {titulo}")
        print(f"  tipo: {f(tipo, ct or 0)} | manchete: {f(tom, cm or 0)} | "
              f"profundidade: {f(prof, cp or 0)} | postura: {f(post, cs or 0)}")
        print(f"  viés político: {f(vies, cv or 0)} | tema principal: {f(tema, ctm or 0)}")
        if just:
            print(f"  justificativa: {just}")
    print()


def exportar_validacao(con, quantos, destino="validacao.csv"):
    """Planilha para anotar à mão SEM ver a resposta do modelo (evita viés)."""
    ids = [r[0] for r in con.execute(
        "SELECT noticia_id FROM classificacoes WHERE erro IS NULL AND texto_insuficiente = 0")]
    escolhidos = random.sample(ids, min(quantos, len(ids)))
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["noticia_id", "portal", "titulo", "link"] +
                   [f"humano_{d}" for d in DIMENSOES])
        for id_ in escolhidos:
            portal, titulo, link = con.execute(
                """SELECT n.portal, n.titulo, COALESCE(t.url_final, n.link_original)
                   FROM noticias n JOIN textos t ON t.noticia_id = n.id
                   WHERE n.id = ?""", (id_,)).fetchone()
            w.writerow([id_, portal, titulo, link] + [""] * len(DIMENSOES))
    print(f"{len(escolhidos)} notícias exportadas para {destino}")
    print("Valores aceitos em cada coluna:")
    for chave, d in DIMENSOES.items():
        print(f"  humano_{chave}: {' / '.join(d['criterios'])}")


def avaliar(con, arquivo):
    with open(arquivo, encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f, delimiter=";"))
    print(f"\nAcerto do modelo em relação à anotação humana ({arquivo}):\n")
    for chave, d in DIMENSOES.items():
        pares = []
        for l in linhas:
            humano = (l.get(f"humano_{chave}") or "").strip().lower().replace(" ", "_")
            if not humano:
                continue
            modelo = con.execute(
                f"SELECT {chave} FROM classificacoes WHERE noticia_id = ?",
                (int(l["noticia_id"]),)).fetchone()
            if modelo and modelo[0]:
                pares.append((humano, modelo[0]))
        if not pares:
            print(f"{d['nome']}: nenhuma anotação preenchida")
            continue
        acertos = sum(h == m for h, m in pares)
        print(f"{d['nome']}: {acertos}/{len(pares)} = {100 * acertos / len(pares):.0f}%")
        rotulos = list(d["criterios"])
        cabecalho = "humano/modelo"
        print(f"  {cabecalho:<16}" + "".join(f"{r:>15}" for r in rotulos))
        matriz = Counter(pares)
        for h in rotulos:
            print(f"  {h:<16}" + "".join(f"{matriz[(h, m)]:>15}" for m in rotulos))
        print()


# ------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Classifica notícias com o Gemini")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--limite", type=int, help="quantas notícias classificar")
    ap.add_argument("--portal", help="classifica só este portal")
    ap.add_argument("--engine", choices=["gemini"], default="gemini", help=argparse.SUPPRESS)
    ap.add_argument("--paralelo", type=int, default=TRABALHADORES,
                    help=f"requisições ao mesmo tempo (padrão {TRABALHADORES})")
    ap.add_argument("--salvar-chave", action="store_true", help="guarda a chave do Gemini")
    ap.add_argument("--stats", action="store_true", help="distribuição dos rótulos por portal")
    ap.add_argument("--exemplos", type=int, metavar="N", help="mostra N classificações")
    ap.add_argument("--validacao", type=int, metavar="N",
                    help="exporta N notícias para anotar à mão (validacao.csv)")
    ap.add_argument("--avaliar", metavar="CSV", help="compara a anotação humana com o modelo")
    ap.add_argument("--temas-grupos", action="store_true",
                    help="define um tema por acontecimento (maioria das notícias do grupo)")
    ap.add_argument("--min-portais", type=int, default=3,
                    help="só acontecimentos com pelo menos N portais (padrão 3, como no site)")
    ap.add_argument("--estimativa", action="store_true",
                    help="mostra quantas notícias faltam, sem chamar o Gemini")
    ap.add_argument("--log", metavar="ARQUIVO", help="grava o log também em arquivo")
    args = ap.parse_args()

    if args.salvar_chave:
        salvar_chave()
        return
    if args.log:
        fh = logging.FileHandler(args.log, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)

    con = sqlite3.connect(args.db)
    preparar_tabela(con)

    if args.estimativa:
        linhas = pendentes(con, None, args.portal, args.min_portais)
        curtas = sum(1 for l in linhas if (l[4] or 0) < MIN_PALAVRAS)
        print(f"{len(linhas)} notícias para classificar "
              f"({curtas} com texto curto, que recebem só o tom da manchete e o tema).")
    elif args.stats:
        mostrar_stats(con)
    elif args.temas_grupos:
        gravar_tema_dos_grupos(con)
    elif args.exemplos:
        mostrar_exemplos(con, args.exemplos)
    elif args.validacao:
        exportar_validacao(con, args.validacao)
    elif args.avaliar:
        avaliar(con, args.avaliar)
    else:
        executar(con, args.limite, args.portal, args.paralelo, args.min_portais)


if __name__ == "__main__":
    main()
