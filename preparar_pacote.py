#!/usr/bin/env python3
"""
Prepara o pacote para quem vai fazer a classificação em outro computador.

O noticias.db completo é grande e tem dezenas de milhares de notícias que não
entram no site. Este script cria um banco reduzido só com o que a
classificação precisa: os acontecimentos com 2+ portais, as notícias deles,
os textos, os temas e as classificações que já existirem.

Gera pacote_classificacao.zip com:
    noticias.db      banco reduzido (o nome é o mesmo para os scripts acharem)
    capas.json       quais fotos são capa (se o capas.zip estiver na pasta)

Mande esse zip para a pessoa (Google Drive, WeTransfer etc.). Ele NÃO vai para
o GitHub, porque tem os textos completos das matérias.

Quando a classificação voltar (a pessoa devolve o noticias.db dela, zipado),
traga as classificações para o seu banco completo:
    python preparar_pacote.py --importar classificado.zip

Uso:
    python preparar_pacote.py                              # cria o pacote
    python preparar_pacote.py --importar classificado.zip  # traz as classificações de volta
"""

import argparse
import json
import os
import sqlite3
import tempfile
import zipfile

TABELAS = ["noticias", "grupos", "noticia_grupo", "noticia_tema", "textos",
           "grupo_tema", "classificacoes"]


def importar(db, arquivo):
    """Copia classificacoes (e grupo_tema) do banco devolvido para o banco completo."""
    pasta = None
    if arquivo.lower().endswith(".zip"):
        pasta = tempfile.mkdtemp()
        with zipfile.ZipFile(arquivo) as z:
            nome = next((n for n in z.namelist() if n.endswith(".db")), None)
            if not nome:
                raise SystemExit("O zip não tem nenhum arquivo .db.")
            z.extract(nome, pasta)
        arquivo = os.path.join(pasta, nome)
    con = sqlite3.connect(db)
    con.execute("ATTACH DATABASE ? AS v", (arquivo,))
    tem = {r[0] for r in con.execute("SELECT name FROM v.sqlite_master WHERE type='table'")}
    if "classificacoes" not in tem:
        raise SystemExit("Esse banco não tem classificações.")
    backup = db.replace(".db", "_antes_importar.db")
    con.execute("DETACH DATABASE v")
    con.execute("VACUUM INTO ?", (backup,))
    con.execute("ATTACH DATABASE ? AS v", (arquivo,))
    sql = con.execute("SELECT sql FROM v.sqlite_master WHERE name='classificacoes'").fetchone()[0]
    con.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
    cols = [r[1] for r in con.execute("PRAGMA v.table_info(classificacoes)")]
    for c, tipo in [(r[1], r[2]) for r in con.execute("PRAGMA v.table_info(classificacoes)")]:
        if c not in {r[1] for r in con.execute("PRAGMA main.table_info(classificacoes)")}:
            con.execute(f"ALTER TABLE main.classificacoes ADD COLUMN {c} {tipo}")
    lista = ", ".join(cols)
    n = con.execute(f"INSERT OR REPLACE INTO main.classificacoes ({lista}) "
                    f"SELECT {lista} FROM v.classificacoes").rowcount
    if "grupo_tema" in tem:
        con.execute("CREATE TABLE IF NOT EXISTS main.grupo_tema (grupo_id INTEGER PRIMARY KEY, "
                    "tema TEXT, votos INTEGER, total INTEGER)")
        con.execute("INSERT OR REPLACE INTO main.grupo_tema (grupo_id, tema, votos, total) "
                    "SELECT grupo_id, tema, votos, total FROM v.grupo_tema")
    con.commit()
    con.execute("DETACH DATABASE v")
    con.close()
    if pasta:
        os.remove(arquivo)
    print(f"{n:,} classificações importadas para {db} (backup em {backup}).")
    print("Próximo passo: python exportar_site.py")


def main():
    ap = argparse.ArgumentParser(description="Prepara o pacote para a classificação")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--min-portais", type=int, default=2)
    ap.add_argument("--saida", default="pacote_classificacao.zip")
    ap.add_argument("--importar", metavar="ARQUIVO",
                    help="traz as classificações de um banco devolvido (.db ou .zip)")
    args = ap.parse_args()

    if args.importar:
        return importar(args.db, args.importar)

    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db} não encontrado. Rode na pasta do projeto.")
    origem = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    existentes = {r[0] for r in origem.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("noticias", "grupos", "noticia_grupo", "textos"):
        if t not in existentes:
            raise SystemExit(f"O banco não tem a tabela {t}. Confira com: python verificar_pasta.py")
    if "grupo_tema" not in existentes:
        print("Aviso: sem a tabela grupo_tema. Rode antes: python definir_temas.py")

    periodo = origem.execute("SELECT MIN(dia_busca), MAX(dia_busca) FROM noticias").fetchone()
    total = origem.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    origem.close()

    pasta = tempfile.mkdtemp()
    destino = os.path.join(pasta, "noticias.db")
    con = sqlite3.connect(destino)
    con.execute("ATTACH DATABASE ? AS o", (args.db,))

    # estrutura igual à do banco completo (com chaves e índices)
    for tipo in ("table", "index"):
        for nome, tabela, sql in con.execute(
                "SELECT name, tbl_name, sql FROM o.sqlite_master WHERE type = ? AND sql IS NOT NULL",
                (tipo,)).fetchall():
            if tabela in TABELAS:
                con.execute(sql)

    con.execute("""CREATE TEMP TABLE g AS SELECT id FROM o.grupos WHERE n_portais >= ?""",
                (args.min_portais,))
    con.execute("""CREATE TEMP TABLE n AS SELECT DISTINCT noticia_id AS id
                   FROM o.noticia_grupo WHERE grupo_id IN (SELECT id FROM g)""")
    filtros = {
        "noticias": "id IN (SELECT id FROM n)",
        "grupos": "id IN (SELECT id FROM g)",
        "noticia_grupo": "grupo_id IN (SELECT id FROM g)",
        "noticia_tema": "noticia_id IN (SELECT id FROM n)",
        "textos": "noticia_id IN (SELECT id FROM n)",
        "grupo_tema": "grupo_id IN (SELECT id FROM g)",
        "classificacoes": "noticia_id IN (SELECT id FROM n)",
    }
    for t in TABELAS:
        if t in existentes:
            con.execute(f"INSERT INTO main.{t} SELECT * FROM o.{t} WHERE {filtros[t]}")

    con.execute("CREATE TABLE pacote_info (chave TEXT PRIMARY KEY, valor TEXT)")
    con.executemany("INSERT INTO pacote_info VALUES (?, ?)", [
        ("periodo_inicio", periodo[0]), ("periodo_fim", periodo[1]),
        ("total_coletadas", str(total)), ("min_portais", str(args.min_portais)),
    ])
    con.commit()
    con.execute("DETACH DATABASE o")
    con.execute("VACUUM")
    n_grupos = con.execute("SELECT COUNT(*) FROM grupos").fetchone()[0]
    n_not = con.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    n_site = con.execute("SELECT COUNT(*) FROM grupos WHERE n_portais >= 3").fetchone()[0]
    con.close()

    with zipfile.ZipFile(args.saida, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(destino, "noticias.db")
        if os.path.exists("capas.zip"):
            with zipfile.ZipFile("capas.zip") as c:
                z.writestr("capas.json", c.read("capas.json"))
        elif os.path.exists("capas.json"):
            z.write("capas.json", "capas.json")
        else:
            print("Aviso: capas.zip não encontrado; o site exportado ficaria sem capas.")
    os.remove(destino)
    os.rmdir(pasta)

    print(f"\n{n_grupos:,} acontecimentos ({n_site:,} com 3+ portais), {n_not:,} notícias")
    print(f"Arquivo: {args.saida} ({os.path.getsize(args.saida) / 1e6:.0f} MB)")
    print("Mande esse arquivo para quem vai classificar. Ele não vai para o GitHub.\n")


if __name__ == "__main__":
    main()
