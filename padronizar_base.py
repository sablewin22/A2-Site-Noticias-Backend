#!/usr/bin/env python3
"""
Deixa a base como se tivesse sido coletada desde o início com as regras atuais:
5 portais, 8 temas, período fixo, e só notícias com texto suficiente.

Etapas:
1. Cópia de segurança do banco (noticias_antes_padronizacao.db).
2. Recoleta: refaz as buscas dos 8 temas em todos os dias do período e grava
   em cada notícia QUAIS temas a trouxeram. Notícias já salvas são
   reconhecidas (pelo link ou pelo portal + título), então links e textos
   já baixados são aproveitados. ~5 h. Se for interrompida, rode de novo:
   ela continua de onde parou.
3. Limpeza:
   - notícias fora do período;
   - notícias que nenhum dos 8 temas trouxe (vinham só da antiga busca geral);
   - duplicatas (versão normal e versão AMP da mesma matéria);
   - páginas sem texto suficiente (menos de 80 palavras: vídeos sem
     transcrição, mapas de apuração, perfis de candidatos);
   - documentos em PDF;
   - páginas automáticas reconhecidas pelo endereço ou pelo título.
4. Remove do embeddings.npz os vetores de notícias apagadas e compacta o banco.

Uso:
    python padronizar_base.py              # tudo (recoleta + limpeza)
    python padronizar_base.py --limpar     # só a limpeza (rode de novo depois
                                           # de baixar textos de notícias novas)
    python padronizar_base.py --simular    # mostra o que a limpeza apagaria, sem apagar
    python padronizar_base.py --limpar --ate-hoje --sem-backup
                                           # limpeza diária (atualização automática):
                                           # o período vai do início até hoje
"""

import argparse
import logging
import os
import re
import shutil
import sqlite3
from datetime import date

import numpy as np

import coletor_noticias as coletor

INICIO = date(2026, 8, 3)
FIM = date(2026, 11, 1)
BACKUP = "noticias_antes_padronizacao.db"
ARQ_EMBEDDINGS = "embeddings.npz"
MIN_PALAVRAS = 80

# Endereços de páginas que não são matérias
PADROES_URL_EXCLUIR = [
    r"\.pdf($|\?)",
    r"/mapas/mapa-de-apuracao/",
    r"/quem-sao-os-candidatos/",
    r"/eleicoes-\d{4}-resultado",
]
# Títulos de páginas automáticas
PADROES_TITULO_EXCLUIR = [
    r"zona eleitoral: resultado",
    r"candidat[oa] a (deputad|senador|governador|president|vereador|prefeit)\w* .* nas elei[cç][oõ]es \d{4}$",
    r"^not[ií]cias ao vivo",
    r"^resultado das elei[cç][oõ]es \d{4} em",
    r"quem s[aã]o os candidatos a",
    r"^(veja|confira) (os|as) (candidatos|resultados|propostas)",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("padronizar")


# ------------------------------------------------------------- utilidades
def chave_url(url: str | None) -> str | None:
    """Mesma matéria em versão normal e AMP vira a mesma chave."""
    if not url:
        return None
    u = url.split("?")[0].split("#")[0].rstrip("/").lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.replace("/google/amp/", "/").replace("/amp/", "/")
    u = re.sub(r"(/amp|\.amp)$", "", u)
    return u


def apagar(con, ids: set[int], motivo: str, simular: bool) -> int:
    if not ids:
        return 0
    log.info("%-58s %6d", motivo, len(ids))
    if simular:
        return len(ids)
    lista = list(ids)
    for i in range(0, len(lista), 500):
        parte = lista[i:i + 500]
        marcas = ",".join("?" * len(parte))
        for tabela in ("textos", "noticia_grupo", "classificacoes", "noticia_tema"):
            if tabela_existe(con, tabela):
                con.execute(f"DELETE FROM {tabela} WHERE noticia_id IN ({marcas})", parte)
        con.execute(f"DELETE FROM noticias WHERE id IN ({marcas})", parte)
    con.commit()
    return len(ids)


def tabela_existe(con, nome):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (nome,)).fetchone() is not None


def meta(con, chave, valor=None):
    con.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)")
    if valor is not None:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (chave, valor))
        con.commit()
        return valor
    linha = con.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return linha[0] if linha else None


# ------------------------------------------------------------- etapas
def fazer_backup(db):
    if os.path.exists(BACKUP):
        log.info("Cópia de segurança já existe (%s), mantida como está.", BACKUP)
        return
    log.info("Fazendo cópia de segurança em %s...", BACKUP)
    shutil.copy2(db, BACKUP)


def recoletar(con, delay):
    if not meta(con, "recoleta_iniciada"):
        log.info("Iniciando recoleta: as buscas antigas serão refeitas com registro de tema.")
        con.execute("DELETE FROM coletas")
        con.commit()
        meta(con, "recoleta_iniciada", date.today().isoformat())
    else:
        log.info("Recoleta já iniciada antes; continuando de onde parou.")
    coletor.executar_periodo(con, INICIO, FIM, delay)


def recoleta_completa(con) -> bool:
    esperado = ((FIM - INICIO).days + 1) * len(coletor.PORTAIS) * len(coletor.TEMAS)
    feito = con.execute(
        "SELECT COUNT(*) FROM coletas WHERE dia BETWEEN ? AND ?",
        (INICIO.isoformat(), FIM.isoformat())).fetchone()[0]
    return feito >= esperado


def limpar(con, simular):
    total = 0
    tem_textos = tabela_existe(con, "textos")
    log.info("Limpeza%s:", " (SIMULAÇÃO, nada será apagado)" if simular else "")

    # 1. portais fora da lista
    portais = list(coletor.PORTAIS)
    ids = {r[0] for r in con.execute(
        f"SELECT id FROM noticias WHERE portal NOT IN ({','.join('?' * len(portais))})",
        portais)}
    total += apagar(con, ids, "portais fora do projeto (ex.: Band)", simular)

    # 2. fora do período
    ids = {r[0] for r in con.execute(
        "SELECT id FROM noticias WHERE dia_busca IS NULL OR dia_busca < ? OR dia_busca > ?",
        (INICIO.isoformat(), FIM.isoformat()))}
    total += apagar(con, ids, f"fora do período {INICIO:%d/%m} a {FIM:%d/%m}", simular)

    # 3. sem nenhum dos 8 temas
    temas = list(coletor.TEMAS)
    ids = {r[0] for r in con.execute(
        f"""SELECT id FROM noticias WHERE id NOT IN (
                SELECT noticia_id FROM noticia_tema
                WHERE tema IN ({','.join('?' * len(temas))}))""", temas)}
    total += apagar(con, ids, "não trazidas por nenhum dos 8 temas", simular)

    # 4. páginas automáticas pelo título
    regex_titulo = re.compile("|".join(PADROES_TITULO_EXCLUIR), re.IGNORECASE)
    ids = {id_ for id_, t in con.execute("SELECT id, titulo FROM noticias")
           if regex_titulo.search(t or "")}
    total += apagar(con, ids, "páginas automáticas (pelo título)", simular)

    # 5. PDFs e páginas automáticas pelo endereço
    regex_url = re.compile("|".join(PADROES_URL_EXCLUIR), re.IGNORECASE)
    sql_url = ("SELECT n.id, COALESCE(t.url_final, n.link_original) FROM noticias n "
               "LEFT JOIN textos t ON t.noticia_id = n.id" if tem_textos else
               "SELECT id, link_original FROM noticias")
    ids = {id_ for id_, u in con.execute(sql_url) if u and regex_url.search(u)}
    total += apagar(con, ids, "PDFs e páginas automáticas (pelo endereço)", simular)

    # 6. texto insuficiente
    if tem_textos:
        ids = {r[0] for r in con.execute(
            "SELECT noticia_id FROM textos WHERE status = 'curto' OR "
            "(status = 'ok' AND COALESCE(n_palavras, 0) < ?)", (MIN_PALAVRAS,))}
        total += apagar(con, ids, f"texto insuficiente (menos de {MIN_PALAVRAS} palavras)",
                        simular)

    # 7. duplicatas (normal x AMP): fica a de melhor texto, com os temas somados
    sql = ("SELECT n.id, n.portal, COALESCE(t.url_final, n.link_original), t.status, "
           "COALESCE(t.n_palavras, 0) FROM noticias n LEFT JOIN textos t ON t.noticia_id = n.id"
           if tem_textos else
           "SELECT id, portal, link_original, NULL, 0 FROM noticias")
    grupos = {}
    for id_, portal, url, status, n in con.execute(sql):
        k = chave_url(url)
        if k:
            grupos.setdefault((portal, k), []).append((status == "ok", n, -id_, id_))
    remover = set()
    for itens in grupos.values():
        if len(itens) > 1:
            itens.sort(reverse=True)
            fica = itens[0][3]
            for *_, id_ in itens[1:]:
                remover.add(id_)
                if not simular:
                    con.execute("INSERT OR IGNORE INTO noticia_tema (noticia_id, tema) "
                                "SELECT ?, tema FROM noticia_tema WHERE noticia_id = ?",
                                (fica, id_))
    total += apagar(con, remover, "duplicatas (mesma matéria em versão normal e AMP)", simular)

    # 8. registros de coleta fora do padrão
    if not simular:
        con.execute("DELETE FROM coletas WHERE dia < ? OR dia > ?",
                    (INICIO.isoformat(), FIM.isoformat()))
        con.execute(f"DELETE FROM coletas WHERE tema NOT IN ({','.join('?' * len(temas))}) "
                    f"OR portal NOT IN ({','.join('?' * len(portais))})", temas + portais)
        con.commit()

    log.info("%-58s %6d", "TOTAL", total)
    return total


def podar_embeddings(con):
    if not os.path.exists(ARQ_EMBEDDINGS):
        return
    dados = np.load(ARQ_EMBEDDINGS)
    ids = dados["ids"]
    validos = {r[0] for r in con.execute("SELECT id FROM noticias")}
    manter = np.array([int(i) in validos for i in ids])
    if manter.all():
        return
    np.savez(ARQ_EMBEDDINGS, ids=ids[manter], vetores=dados["vetores"][manter])
    log.info("embeddings.npz: %d vetores de notícias apagadas removidos", (~manter).sum())


def relatorio(con):
    print("\n" + "=" * 60)
    print(f"Período: {INICIO:%d/%m/%Y} a {FIM:%d/%m/%Y}")
    total = con.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    print(f"Notícias: {total:,}")
    print("\nPor portal:")
    for portal, n in con.execute(
            "SELECT portal, COUNT(*) FROM noticias GROUP BY portal ORDER BY 2 DESC"):
        print(f"  {portal:<16} {n:>8,}")
    print("\nPor tema (uma notícia pode ter mais de um):")
    for tema, n in con.execute(
            "SELECT tema, COUNT(*) FROM noticia_tema GROUP BY tema ORDER BY 2 DESC"):
        print(f"  {tema:<16} {n:>8,}")
    print("=" * 60)


# ------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Padroniza a base de notícias")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--limpar", action="store_true", help="só a limpeza, sem recoleta")
    ap.add_argument("--simular", action="store_true", help="mostra o que seria apagado")
    ap.add_argument("--delay", type=float, default=4.0, help="segundos entre buscas")
    ap.add_argument("--ate-hoje", action="store_true",
                    help="o período vai até hoje (para a atualização diária)")
    ap.add_argument("--sem-backup", action="store_true", help="não faz a cópia de segurança")
    ap.add_argument("--log", metavar="ARQUIVO", help="grava o log também em arquivo")
    args = ap.parse_args()

    global FIM
    if args.ate_hoje:
        FIM = max(FIM, date.today())

    if args.log:
        fh = logging.FileHandler(args.log, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(fh)

    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db} não encontrado.")

    if not args.simular and not args.sem_backup:
        fazer_backup(args.db)
    con = coletor.abrir_banco(args.db)

    if not args.limpar and not args.simular:
        recoletar(con, args.delay)

    if args.simular and not recoleta_completa(con):
        log.warning("A recoleta ainda não foi feita: a simulação vai contar quase todas as "
                    "notícias como 'sem tema'. Ela só faz sentido depois da recoleta.")

    if not args.simular and not recoleta_completa(con):
        log.warning("A recoleta ainda não terminou (houve falhas de rede?). "
                    "Rode de novo: python padronizar_base.py")
        log.warning("A limpeza só roda com a recoleta completa, para não apagar notícias "
                    "cujos temas ainda não foram registrados.")
        return

    limpar(con, args.simular)
    if not args.simular:
        podar_embeddings(con)
        log.info("Compactando o banco...")
        con.execute("VACUUM")
        relatorio(con)
        print("\nPróximo passo: python agrupar_noticias.py\n")


if __name__ == "__main__":
    main()
