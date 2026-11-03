#!/usr/bin/env python3
"""
Agrupa notícias de portais diferentes que falam sobre o mesmo fato,
usando só o título. Lê e grava no mesmo noticias.db do coletor.

Como funciona:
1. Transforma cada título em um vetor (embedding) com um modelo multilíngue.
   Os vetores ficam guardados em embeddings.npz, então só títulos novos
   são processados nas próximas execuções.
2. Percorre as notícias dia a dia. Cada notícia entra no grupo mais parecido
   que começou nas últimas 48 h, se a semelhança passar do limiar.
   As que não encontram grupo são agrupadas entre si, e o que sobrar vira
   um grupo novo.
3. Grava as tabelas `grupos` e `noticia_grupo`. Toda execução refaz o
   agrupamento do zero (leva poucos minutos), então dá para testar limiares.

Instalação:
    pip install sentence-transformers

Uso:
    python agrupar_noticias.py                   # agrupa tudo (limiar padrão 0.80)
    python agrupar_noticias.py --limiar 0.78     # mais rigoroso: grupos menores e mais certeiros
    python agrupar_noticias.py --amostra 40      # exporta 40 grupos para conferir à mão
    python agrupar_noticias.py --stats           # resumo dos grupos
"""

import argparse
import csv
import logging
import os
import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ARQ_EMBEDDINGS = "embeddings.npz"
LIMIAR_PADRAO = 0.80
MAX_POR_PORTAL = 3  # notícias de um mesmo portal num mesmo grupo
MIN_PALAVRAS = 4    # títulos menores são páginas de seção/tag, não matérias
JANELA_HORAS = 48
SEGUNDOS_POR_LINK = 3  # tempo médio do --decode, para a estimativa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agrupar")


# ---------------------------------------------------------------- banco
def preparar_tabelas(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS grupos (
            id INTEGER PRIMARY KEY,
            titulo TEXT,
            inicio TEXT,
            fim TEXT,
            n_noticias INTEGER,
            n_portais INTEGER,
            portais TEXT
        );
        CREATE TABLE IF NOT EXISTS noticia_grupo (
            noticia_id INTEGER PRIMARY KEY,
            grupo_id INTEGER NOT NULL,
            similaridade REAL
        );
        CREATE INDEX IF NOT EXISTS idx_ng_grupo ON noticia_grupo(grupo_id);
        """
    )


def carregar_noticias(con):
    linhas = con.execute(
        """SELECT id, portal, titulo, publicado_em FROM noticias
           WHERE publicado_em IS NOT NULL AND titulo <> ''
           ORDER BY publicado_em"""
    ).fetchall()
    antes = len(linhas)
    linhas = [r for r in linhas if len(r[2].split()) >= MIN_PALAVRAS]
    if antes - len(linhas):
        log.info("%d títulos com menos de %d palavras ignorados (páginas de seção/tag)",
                 antes - len(linhas), MIN_PALAVRAS)
    ids = np.array([r[0] for r in linhas], dtype=np.int64)
    portais = [r[1] for r in linhas]
    titulos = [r[2] for r in linhas]
    datas = [datetime.fromisoformat(r[3]) for r in linhas]
    return ids, portais, titulos, datas


# ---------------------------------------------------------------- embeddings
def carregar_modelo():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        log.error("Instale com: pip install sentence-transformers")
        raise SystemExit(1)
    log.info("Carregando o modelo (na primeira vez ele é baixado, ~500 MB)...")
    return SentenceTransformer(MODELO)


def calcular_embeddings(ids, titulos, arquivo, lote: int, codificar=None):
    """Devolve uma matriz (n x d) normalizada, na mesma ordem de `ids`.
    Reaproveita os vetores já calculados em `arquivo`."""
    cache = {}
    if os.path.exists(arquivo):
        dados = np.load(arquivo)
        cache = dict(zip(dados["ids"].tolist(), dados["vetores"]))
        log.info("%d títulos já tinham vetor calculado", len(cache))

    faltando = [i for i, id_ in enumerate(ids.tolist()) if id_ not in cache]
    if faltando:
        log.info("Calculando vetores de %d títulos novos...", len(faltando))
        if codificar is None:
            modelo = carregar_modelo()

            def codificar(textos):
                return modelo.encode(textos, batch_size=lote, show_progress_bar=True,
                                     normalize_embeddings=True, convert_to_numpy=True)

        novos = codificar([titulos[i] for i in faltando])
        for i, vetor in zip(faltando, novos):
            cache[int(ids[i])] = vetor.astype(np.float16)
        np.savez(arquivo,
                 ids=np.array(list(cache.keys()), dtype=np.int64),
                 vetores=np.stack(list(cache.values())).astype(np.float16))
        log.info("Vetores salvos em %s", arquivo)

    return np.stack([cache[int(id_)] for id_ in ids]).astype(np.float32)


# ---------------------------------------------------------------- agrupamento
def normalizar(m):
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    n[n == 0] = 1
    return m / n


def agrupar_entre_si(vetores, limiar):
    """Agrupamento hierárquico (ligação completa) das notícias de um mesmo dia
    que não encontraram grupo existente. Ligação completa exige que TODOS os
    pares do grupo passem do limiar, o que evita o efeito "corrente"
    (A parece B, B parece C, mas A não parece C)."""
    if len(vetores) == 1:
        return np.array([1])
    from scipy.cluster.hierarchy import fcluster, linkage
    z = linkage(vetores, method="complete", metric="cosine")
    return fcluster(z, t=1 - limiar, criterion="distance")


def agrupar(vetores, datas, portais, limiar, janela_horas, max_por_portal):
    """Devolve, para cada notícia, (grupo, similaridade com o grupo).

    Regras para uma notícia entrar num grupo existente:
    - o grupo começou há no máximo `janela_horas` (conta a partir da PRIMEIRA
      notícia, então nenhum grupo fica aberto para sempre);
    - a semelhança com o centro do grupo E com a notícia que o fundou passa
      do limiar (impede que o grupo vá "derivando" para outro assunto);
    - o portal ainda não tem `max_por_portal` notícias no grupo.
    """
    n = len(datas)
    grupo_de = np.full(n, -1, dtype=np.int64)
    sim_de = np.zeros(n, dtype=np.float32)

    somas = []      # soma dos vetores de cada grupo
    sementes = []   # vetor de quem fundou o grupo
    inicio = []     # data da primeira notícia do grupo
    contagem = []   # notícias por portal em cada grupo
    janela = timedelta(hours=janela_horas)

    por_dia = defaultdict(list)
    for i, d in enumerate(datas):
        por_dia[d.date()].append(i)

    for dia in sorted(por_dia):
        idx = np.array(sorted(por_dia[dia], key=lambda i: datas[i]))
        inicio_dia = datetime.combine(dia, datetime.min.time(), tzinfo=datas[idx[0]].tzinfo)
        ativos = [g for g in range(len(somas)) if inicio_dia - inicio[g] <= janela]

        sem_grupo = []
        if ativos:
            centroides = normalizar(np.stack([somas[g] for g in ativos]))
            sims = vetores[idx] @ centroides.T
            sims_semente = vetores[idx] @ np.stack([sementes[g] for g in ativos]).T
            aptos = np.minimum(sims, sims_semente)
            for k, i in enumerate(idx):
                candidatos = np.where(aptos[k] >= limiar)[0]
                escolhido = None
                for j in candidatos[np.argsort(-aptos[k][candidatos])]:
                    g = ativos[j]
                    if contagem[g][portais[i]] < max_por_portal:
                        escolhido = g
                        break
                if escolhido is None:
                    sem_grupo.append(i)
                    continue
                grupo_de[i], sim_de[i] = escolhido, sims[k, ativos.index(escolhido)]
                somas[escolhido] = somas[escolhido] + vetores[i]
                contagem[escolhido][portais[i]] += 1
        else:
            sem_grupo = list(idx)

        if sem_grupo:
            sem_grupo = np.array(sem_grupo)
            rotulos = agrupar_entre_si(vetores[sem_grupo], limiar)
            blocos = []
            for r in np.unique(rotulos):
                membros = sem_grupo[rotulos == r]
                # aplica o limite por portal também aos grupos novos: ficam as
                # notícias mais próximas do centro; as outras viram grupos à parte
                centro = normalizar(vetores[membros].sum(axis=0))
                por_portal = defaultdict(list)
                for i in membros:
                    por_portal[portais[i]].append(i)
                ficam, sobram = [], []
                for lista in por_portal.values():
                    lista.sort(key=lambda i: -float(vetores[i] @ centro))
                    ficam += lista[:max_por_portal]
                    sobram += lista[max_por_portal:]
                blocos.append(np.array(ficam))
                blocos.extend(np.array([i]) for i in sobram)
            for membros in blocos:
                g = len(somas)
                somas.append(vetores[membros].sum(axis=0))
                centro = normalizar(somas[g])
                sementes.append(centro)
                inicio.append(min(datas[i] for i in membros))
                contagem.append(defaultdict(int))
                for i in membros:
                    grupo_de[i] = g
                    sim_de[i] = float(vetores[i] @ centro)
                    contagem[g][portais[i]] += 1

    return grupo_de, sim_de


# ---------------------------------------------------------------- gravação
def gravar(con, ids, portais, titulos, datas, grupo_de, sim_de):
    membros = defaultdict(list)
    for i, g in enumerate(grupo_de.tolist()):
        membros[g].append(i)

    con.execute("DELETE FROM grupos")
    con.execute("DELETE FROM noticia_grupo")

    linhas_grupos, linhas_ng = [], []
    novo_id = 0
    for g, itens in membros.items():
        novo_id += 1
        itens.sort(key=lambda i: datas[i])
        nomes = sorted({portais[i] for i in itens})
        linhas_grupos.append((
            novo_id, titulos[itens[0]],
            datas[itens[0]].isoformat(), datas[itens[-1]].isoformat(),
            len(itens), len(nomes), ", ".join(nomes),
        ))
        linhas_ng.extend((int(ids[i]), novo_id, round(float(sim_de[i]), 4)) for i in itens)

    con.executemany("INSERT INTO grupos VALUES (?,?,?,?,?,?,?)", linhas_grupos)
    con.executemany("INSERT INTO noticia_grupo VALUES (?,?,?)", linhas_ng)
    con.commit()


# ---------------------------------------------------------------- relatórios
def mostrar_stats(con):
    total = con.execute("SELECT COUNT(*) FROM grupos").fetchone()[0]
    if not total:
        print("Nenhum grupo ainda. Rode: python agrupar_noticias.py")
        return
    print(f"\nGrupos: {total}")
    print("\nPor quantidade de portais:")
    for n_port, qtd, n_not in con.execute(
            """SELECT n_portais, COUNT(*), SUM(n_noticias) FROM grupos
               GROUP BY n_portais ORDER BY n_portais"""):
        print(f"  {n_port} portal(is): {qtd:>7} grupos  ({n_not} notícias)")

    relevantes = con.execute(
        """SELECT COUNT(*) FROM noticia_grupo ng JOIN grupos g ON g.id = ng.grupo_id
           WHERE g.n_portais >= 2""").fetchone()[0]
    sem_link = con.execute(
        """SELECT COUNT(*) FROM noticia_grupo ng
           JOIN grupos g ON g.id = ng.grupo_id
           JOIN noticias n ON n.id = ng.noticia_id
           WHERE g.n_portais >= 2 AND n.link_original IS NULL""").fetchone()[0]
    horas = sem_link * SEGUNDOS_POR_LINK / 3600
    print(f"\nNotícias em grupos com 2+ portais: {relevantes}")
    print(f"Dessas, ainda sem link original: {sem_link} (~{horas:.1f} h de --decode)\n")

    print("Maiores grupos:")
    for titulo, n_not, portais in con.execute(
            "SELECT titulo, n_noticias, portais FROM grupos ORDER BY n_noticias DESC LIMIT 5"):
        print(f"  [{n_not}] {titulo[:90]}  ({portais})")
    print()


def exportar_amostra(con, quantos: int, destino: str):
    grupos = [r[0] for r in con.execute("SELECT id FROM grupos WHERE n_portais >= 2")]
    if not grupos:
        print("Nenhum grupo com 2+ portais.")
        return
    escolhidos = random.sample(grupos, min(quantos, len(grupos)))
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["grupo", "portal", "publicado_em", "similaridade", "titulo",
                    "grupo_correto (s/n)", "observacao"])
        for g in escolhidos:
            for portal, data, sim, titulo in con.execute(
                    """SELECT n.portal, n.publicado_em, ng.similaridade, n.titulo
                       FROM noticia_grupo ng JOIN noticias n ON n.id = ng.noticia_id
                       WHERE ng.grupo_id = ? ORDER BY n.publicado_em""", (g,)):
                w.writerow([g, portal, data[:16], f"{sim:.2f}", titulo, "", ""])
            w.writerow([])
    print(f"{len(escolhidos)} grupos exportados para {destino}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Agrupa notícias do mesmo fato pelo título")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--limiar", type=float, default=LIMIAR_PADRAO,
                    help=f"semelhança mínima para entrar no grupo, de 0 a 1 (padrão {LIMIAR_PADRAO})")
    ap.add_argument("--janela", type=int, default=JANELA_HORAS,
                    help=f"horas que um grupo fica aberto a notícias novas (padrão {JANELA_HORAS})")
    ap.add_argument("--max-por-portal", type=int, default=MAX_POR_PORTAL,
                    help=f"máximo de notícias de um portal por grupo (padrão {MAX_POR_PORTAL})")
    ap.add_argument("--lote", type=int, default=64, help="títulos por lote no modelo")
    ap.add_argument("--amostra", type=int, metavar="N",
                    help="exporta N grupos com 2+ portais para amostra_grupos.csv e sai")
    ap.add_argument("--stats", action="store_true", help="mostra o resumo dos grupos e sai")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    preparar_tabelas(con)

    if args.stats:
        mostrar_stats(con)
        return
    if args.amostra:
        exportar_amostra(con, args.amostra, "amostra_grupos.csv")
        return

    ids, portais, titulos, datas = carregar_noticias(con)
    log.info("%d notícias carregadas", len(ids))
    vetores = calcular_embeddings(ids, titulos, ARQ_EMBEDDINGS, args.lote)

    log.info("Agrupando (limiar %.2f, janela %d h)...", args.limiar, args.janela)
    grupo_de, sim_de = agrupar(vetores, datas, portais, args.limiar, args.janela,
                               args.max_por_portal)
    gravar(con, ids, portais, titulos, datas, grupo_de, sim_de)
    log.info("Pronto.")
    mostrar_stats(con)


if __name__ == "__main__":
    main()
