#!/usr/bin/env python3
"""
Define o tema de cada acontecimento pelo vocabulário dos títulos e do começo
dos textos das notícias (ex.: "Lula", "STF", "Datafolha" contam para política;
"Fórmula 1", "Libertadores" para esporte). Não usa modelo nem internet.

Para cada acontecimento soma: termos no título (peso 3) + termos no começo do
texto (peso 1), em todas as notícias do grupo. Fica o tema com mais pontos;
sem nenhum termo, vale o tema da busca.

Grava na tabela grupo_tema, que o exportar_site.py usa. Quando a classificação
(tema principal) rodar, ela substitui esta tabela.

Rode de novo sempre que o agrupar_noticias.py refizer os grupos.

Uso:
    python definir_temas.py                          # define os temas
    python definir_temas.py --amostra 30             # confere 30 resultados
    python definir_temas.py --amostra 30 --tema esporte
"""

import argparse
import random
import sqlite3


import re
import unicodedata
from collections import Counter

TERMOS = {
    "politica": [
        "lula", "bolsonaro", "flavio", "boulos", "haddad", "tarcisio", "alckmin", "zema",
        "caiado", "ratinho", "moraes", "fachin", "gonet", "mendonca", "gilmar", "zanin",
        "barroso", "nunes marques", "fux", "motta", "alcolumbre", "pacheco",
        "stf", "supremo", "tse", "tre", "pgr", "agu", "cgu", "tcu", "pf ", "policia federal",
        "senado", "senador", "camara dos deputados", "deputad", "congresso", "plenario",
        "ministro", "ministerio da justica", "planalto", "presidente da republica",
        "governador", "prefeit", "vereador", "eleic", "eleitor", "urna", "turno", "candidat",
        "campanha", "voto", "votacao", "datafolha", "quaest", "atlasintel", "ipec", "pesquisa eleitoral",
        "partido", "pt ", "pl ", "psdb", "mdb", "psol", "uniao brasil", "republicanos",
        "pec", "projeto de lei", "sanciona", "veta", "decreto", "impeachment", "cpi", "cpmi",
        "inquerito", "delacao", "trump", "putin", "zelensky", "netanyahu", "milei", "maduro",
        "casa branca", "itamaraty", "diplomat", "embaixad", "otan", "onu", "kremlin", "gaza",
        "israel", "hamas", "ucrania", "russia", "golpe", "anistia", "reeleit", "posse",
    ],
    "economia": [
        "inflacao", "ipca", "igp", "selic", "juros", "copom", "banco central", "dolar", "cambio",
        "bolsa", "ibovespa", "acoes", "pib", "recessao", "desemprego", "emprego", "caged",
        "salario minimo", "imposto", "tribut", "receita federal", "arrecadacao", "deficit",
        "superavit", "contas publicas", "orcamento", "divida", "fiscal", "tesouro", "fazenda",
        "petrobras", "petroleo", "combustiv", "gasolina", "diesel", "querosene", "energia eletrica",
        "tarifa", "empresa", "lucro", "prejuizo", "fatur", "mercado", "investimento", "credito",
        "banco", "pix", "nubank", "itau", "bradesco", "caixa economica", "inss", "aposentad",
        "bolsa familia", "mega-sena", "loteria", "quina", "lotofacil", "premio de r$",
        "recuperacao judicial", "falencia", "varejo", "industria", "agronegocio", "exporta",
        "importa", "tarifaco", "comercio", "consumidor", "preco", "custo", "cripto", "bitcoin",
        "binance", "greve", "fgts", "seguro-desemprego", "reforma tributaria",
    ],
    "saude": [
        "saude", "sus", "hospital", "medic", "doenca", "virus", "vacina", "vacinacao", "dengue",
        "chikungunya", "zika", "covid", "gripe", "influenza", "sarampo", "mpox", "febre",
        "cancer", "tumor", "diabetes", "obesidade", "anvisa", "remedio", "medicamento",
        "tratamento", "cirurgia", "internad", "uti", "surto", "epidemia", "pandemia",
        "paciente", "enfermeir", "planos de saude", "fiocruz", "butantan", "oms", "transplante",
        "saude mental", "depressao", "autismo", "alzheimer", "infarto", "avc", "pneumonia",
        "bacteria", "infeccao", "contaminad", "intoxica", "metanol",
    ],
    "educacao": [
        "educacao", "escola", "escolar", "ensino", "aluno", "estudante", "professor", "enem",
        "vestibular", "fuvest", "sisu", "prouni", "fies", "universidade", "faculdade",
        "mec", "inep", "ideb", "pisa", "alfabetiza", "creche", "sala de aula", "graduacao",
        "pos-graduacao", "bolsa de estudo", "capes", "cnpq", "reitor", "matricula", "curso",
        "provas", "redacao do enem", "ensino medio", "ensino superior", "educacional",
    ],
    "meio_ambiente": [
        "meio ambiente", "ambiental", "clima", "climatic", "aquecimento global", "cop3",
        "desmatamento", "amazonia", "pantanal", "cerrado", "mata atlantica", "queimada",
        "incendio florestal", "fumaca", "poluicao", "emissao", "carbono", "chuva", "chuvas",
        "temporal", "enchente", "inundac", "alagamento", "deslizamento", "seca", "estiagem",
        "calor", "onda de frio", "frente fria", "temperatura", "ciclone", "furacao", "tornado",
        "terremoto", "tsunami", "vulcao", "defesa civil", "inmet", "ibama", "icmbio",
        "reciclagem", "lixo", "saneamento", "especie", "animais", "baleia", "onca",
        "rio ", "nivel do rio", "barragem", "oceano", "geleira", "sustentav", "foz do amazonas",
    ],
    "esporte": [
        "futebol", "gol", "campeonato", "brasileirao", "libertadores", "sul-americana",
        "copa do brasil", "copa do mundo", "selecao brasileira", "selecao feminina", "tecnico",
        "jogador", "atacante", "zagueiro", "goleiro", "partida", "rodada", "classico",
        "flamengo", "palmeiras", "corinthians", "sao paulo fc", "santos", "vasco", "botafogo",
        "fluminense", "gremio", "internacional", "cruzeiro", "atletico", "bahia", "fortaleza",
        "mirassol", "bragantino", "ancelotti", "neymar", "vinicius", "richarlison",
        "formula 1", "f1", "gp ", "grande premio", "piloto", "corrida", "tenis", "wta", "atp",
        "volei", "basquete", "nba", "nfl", "olimpi", "paralimpi", "atleta", "medalha",
        "maratona", "skate", "surfe", "ginastica", "rebeca andrade", "ufc", "boxe", "fifa", "cbf",
        "torcida", "estadio", "escalac", "onde assistir", "placar", "melhores momentos",
    ],
    "cultura": [
        "cultura", "cultural", "cinema", "filme", "serie", "novela", "ator", "atriz", "cantor",
        "cantora", "musica", "album", "show", "turne", "festival", "rock in rio", "lollapalooza",
        "carnaval", "escola de samba", "rainha de bateria", "desfile", "museu", "exposicao",
        "teatro", "peca", "livro", "escritor", "literatura", "premio jabuti", "oscar", "grammy",
        "emmy", "bienal", "artista", "arte", "dança", "danca", "streaming", "netflix", "globoplay",
        "reality", "bbb", "celebridade", "famos", "influenciador", "youtuber", "morre aos",
        "morre o", "morre a", "estreia", "documentario", "patrimonio", "halloween",
    ],
}


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
    return " " + re.sub(r"[^a-z0-9$\- ]+", " ", t) + " "


_RE = {tema: [re.compile(r"(?<![a-z0-9])" + re.escape(normalizar(p).strip()))
              for p in termos] for tema, termos in TERMOS.items()}


def pontos(titulo: str, trecho: str = "") -> Counter:
    c = Counter()
    tt, tr = normalizar(titulo), normalizar(trecho)
    for tema, regs in _RE.items():
        for r in regs:
            if r.search(tt):
                c[tema] += 3
            if r.search(tr):
                c[tema] += 1
    return c


ORDEM = ["politica", "economia", "saude", "educacao", "meio_ambiente", "esporte", "cultura"]


def tema_do_grupo(noticias, tema_busca=None):
    """noticias: lista de (titulo, trecho). Devolve o tema com mais pontos."""
    total = Counter()
    for titulo, trecho in noticias:
        total.update(pontos(titulo, trecho))
    if not total:
        return tema_busca
    melhor = max(total.values())
    empatados = [t for t in ORDEM if total[t] == melhor]
    if tema_busca in empatados:
        return tema_busca
    return empatados[0]


def main():
    ap = argparse.ArgumentParser(description="Define o tema de cada acontecimento")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--min-portais", type=int, default=2)
    ap.add_argument("--amostra", type=int, metavar="N", help="mostra N resultados e sai")
    ap.add_argument("--tema", help="com --amostra, mostra só este tema")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    if args.amostra:
        linhas = con.execute(
            """SELECT g.titulo, t.tema FROM grupo_tema t JOIN grupos g ON g.id = t.grupo_id
               WHERE g.n_portais >= 3""").fetchall()
        if args.tema:
            linhas = [l for l in linhas if l[1] == args.tema]
        for titulo, tema in random.sample(linhas, min(args.amostra, len(linhas))):
            print(f"  {tema:<14} {titulo[:95]}")
        return

    tem_textos = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='textos'").fetchone()
    grupos = con.execute("SELECT id FROM grupos WHERE n_portais >= ?",
                         (args.min_portais,)).fetchall()
    resultado, antes = [], {}
    for (gid,) in grupos:
        linhas = con.execute(
            f"""SELECT n.id, n.titulo, {"substr(COALESCE(t.texto, ''), 1, 300)" if tem_textos else "''"}
                FROM noticia_grupo ng JOIN noticias n ON n.id = ng.noticia_id
                {"LEFT JOIN textos t ON t.noticia_id = n.id" if tem_textos else ""}
                WHERE ng.grupo_id = ?""", (gid,)).fetchall()
        busca = Counter(t for (nid, _, _) in linhas for (t,) in con.execute(
            "SELECT tema FROM noticia_tema WHERE noticia_id = ?", (nid,)))
        tema_busca = busca.most_common(1)[0][0] if busca else None
        tema = tema_do_grupo([(titulo, texto) for (_, titulo, texto) in linhas], tema_busca)
        if tema:
            resultado.append((gid, tema))
            if tema_busca:
                antes[gid] = tema_busca

    con.execute("DROP TABLE IF EXISTS grupo_tema")
    con.execute("CREATE TABLE grupo_tema (grupo_id INTEGER PRIMARY KEY, tema TEXT, "
                "votos INTEGER, total INTEGER)")
    con.executemany("INSERT INTO grupo_tema (grupo_id, tema) VALUES (?, ?)", resultado)
    con.commit()

    mudou = sum(1 for g, t in resultado if antes.get(g) and antes[g] != t)
    print(f"\n{len(resultado)} acontecimentos com tema definido "
          f"({mudou} diferentes do tema da busca)")
    for t, q in Counter(t for _, t in resultado).most_common():
        print(f"  {t:<14} {q:>5}")
    print("\nPara conferir: python definir_temas.py --amostra 30")
    print("Depois: python exportar_site.py\n")


if __name__ == "__main__":
    main()
