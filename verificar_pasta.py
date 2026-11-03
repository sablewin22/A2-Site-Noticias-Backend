#!/usr/bin/env python3
"""Confere se os arquivos do projeto são as versões certas e mostra o estado do banco.
Só lê, não muda nada. Pode rodar enquanto o --decode está em andamento."""

import importlib.util
import os
import sqlite3
import sys

ARQUIVOS = {
    "coletor_noticias.py": [
        ('res.get("success")', "correção do decodificador"),
        ("--todas", "--decode só nas notícias agrupadas"),
        ('"cultura"', "7 temas (com política e cultura)"),
        ("noticia_tema", "registra o tema de cada notícia"),
    ],
    "agrupar_noticias.py": [
        ("LIMIAR_PADRAO = 0.80", "limiar 0.80"),
        ("MIN_PALAVRAS = 4", "ignora títulos de 1-3 palavras"),
        ("MAX_POR_PORTAL", "limite por portal"),
    ],
    "baixar_textos.py": [
        ("corpo_ld_json", "fonte extra de texto"),
        ("--diagnostico", "modo diagnóstico"),
        ("imagem_url", "guarda a foto"),
    ],
    "padronizar_base.py": [
        ("PADROES_URL_EXCLUIR", "regras de limpeza"),
    ],
    "classificar_noticias.py": [
        ("DIMENSOES", "4 dimensões + tema principal"),
        ("--estimativa", "versão para a entrega (só 3+ portais)"),
    ],
    "exportar_site.py": [
        ("RE_CREDITO_AGENCIA", "limpa créditos de foto"),
        ('"fid"', "fotos identificadas pelo link"),
        ("ler_capas", "usa as capas do baixar_capas.py"),
    ],
    "definir_temas.py": [
        ("TERMOS", "tema pelo vocabulário do título"),
    ],
    "baixar_capas.py": [
        ("capas.json", "capas na melhor qualidade"),
    ],
    "preparar_pacote.py": [
        ("--importar", "pacote para a classificação e volta"),
    ],
}
NAO_PODE_TER = {
    "coletor_noticias.py": [('"Band"', "Band removida"), ('"geral": ""', "busca geral removida"),
                            ('"tecnologia"', "tecnologia removida")],
}

ok = lambda b: "OK " if b else "XX "

print("=" * 60)
print(f"Python {sys.version.split()[0]}  |  pasta: {os.getcwd()}")
print("=" * 60)

print("\nARQUIVOS")
for nome, marcas in ARQUIVOS.items():
    if not os.path.exists(nome):
        print(f"  XX {nome}: NÃO ENCONTRADO")
        continue
    texto = open(nome, encoding="utf-8").read()
    print(f"  {nome} ({os.path.getsize(nome):,} bytes)")
    for marca, desc in marcas:
        print(f"     {ok(marca in texto)}{desc}")
    for marca, desc in NAO_PODE_TER.get(nome, []):
        print(f"     {ok(marca not in texto)}{desc}")

sobras = [f for f in os.listdir(".") if f.endswith(".py") and "(" in f]
antigos = [f for f in ("baixar_fotos.py", "remover_tema.py", "limpar_pasta.py",
                       "temas_vocabulario.py", "fotos.zip") if os.path.exists(f)]
if antigos:
    print(f"  !! não são mais usados, podem ser apagados: {', '.join(antigos)}")
if sobras:
    print(f"  !! arquivos com (1) no nome, podem ser apagados: {', '.join(sobras)}")
if os.path.exists(".typesafe_key"):
    print("  .typesafe_key presente (não enviar ao GitHub)")

print("\nBIBLIOTECAS")
for mod in ("feedparser", "requests", "googlenewsdecoder", "trafilatura",
            "numpy", "scipy", "sentence_transformers", "torch", "PIL", "typesafe_sdk"):
    print(f"  {ok(importlib.util.find_spec(mod) is not None)}{mod}")

print("\nBANCO (noticias.db)")
if not os.path.exists("noticias.db"):
    print("  XX noticias.db não encontrado")
else:
    con = sqlite3.connect("file:noticias.db?mode=ro", uri=True, timeout=30)
    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    print(f"  tamanho: {os.path.getsize('noticias.db') / 1e6:.0f} MB | tabelas: {', '.join(sorted(tabelas))}")
    total, ini, fim = con.execute(
        "SELECT COUNT(*), MIN(publicado_em), MAX(publicado_em) FROM noticias").fetchone()
    print(f"  notícias: {total:,} | de {str(ini)[:10]} a {str(fim)[:10]}")
    print("  por portal:")
    for portal, n, links in con.execute(
            "SELECT portal, COUNT(*), COUNT(link_original) FROM noticias GROUP BY portal ORDER BY 2 DESC"):
        print(f"     {portal:<16} {n:>7,}  (com link original: {links:,})")
    if "grupos" in tabelas:
        g, n_rel, sem = con.execute(
            """SELECT (SELECT COUNT(*) FROM grupos),
                      COUNT(*), SUM(n.link_original IS NULL)
               FROM noticia_grupo ng JOIN grupos gr ON gr.id = ng.grupo_id
               JOIN noticias n ON n.id = ng.noticia_id WHERE gr.n_portais >= 2""").fetchone()
        print(f"  grupos: {g:,} | notícias em grupos 2+ portais: {n_rel:,} | sem link ainda: {sem or 0:,}")
    if "noticia_tema" in tabelas:
        print("  temas: " + ", ".join(f"{t}: {n:,}" for t, n in con.execute(
            "SELECT tema, COUNT(*) FROM noticia_tema GROUP BY tema ORDER BY 2 DESC")))
    if "textos" in tabelas:
        print("  textos: " + ", ".join(f"{s}: {n}" for s, n in con.execute(
            "SELECT status, COUNT(*) FROM textos GROUP BY status")))
    if "grupo_tema" in tabelas:
        n = con.execute("SELECT COUNT(*) FROM grupo_tema").fetchone()[0]
        print(f"  acontecimentos com tema definido: {n:,}")
    if "classificacoes" in tabelas:
        n = con.execute("SELECT COUNT(*) FROM classificacoes").fetchone()[0]
        print(f"  classificações: {n}")

print("\nOUTROS ARQUIVOS")
for nome in ("embeddings.npz", "dados_site.json", "capas.zip", "pacote_classificacao.zip",
             "noticias_com_band.db", "noticias_antes_padronizacao.db",
             "noticias_antes_remover_tecnologia.db", "noticias_antes_importar.db"):
    if os.path.exists(nome):
        print(f"  {nome} ({os.path.getsize(nome) / 1e6:.1f} MB)")
print()
