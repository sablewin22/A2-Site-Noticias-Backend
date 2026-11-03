# -*- coding: utf-8 -*-
"""
Script de Migração: SQLite (noticias.db) -> Supabase (Nuvem)
Execução: python migrar_para_supabase.py --db noticias.db
"""

import os
import sqlite3
import requests
import json
import argparse
from datetime import datetime

# Substitua ou defina nas variáveis de ambiente
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://seu-projeto.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sua-anon-ou-service-role-key")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def migrar(banco_sqlite):
    if not os.path.exists(banco_sqlite):
        print(f"Erro: Arquivo {banco_sqlite} não encontrado.")
        print("Coloque o noticias.db recebido da Sabrina na pasta do projeto e tente novamente.")
        return

    con = sqlite3.connect(banco_sqlite)
    con.row_factory = sqlite3.Row

    print("Conectado ao SQLite local. Lendo notícias...")
    cur = con.cursor()

    # Busca as notícias com os textos associados
    sql = """
        SELECT n.id, n.portal, n.titulo, n.link_original, n.dia_busca, n.publicado_em,
               t.texto, t.url_final, t.n_palavras, t.opiniao
        FROM noticias n
        LEFT JOIN textos t ON t.noticia_id = n.id
    """
    try:
        linhas = cur.execute(sql).fetchall()
    except Exception as e:
        print(f"Erro ao ler tabelas do SQLite: {e}")
        return

    print(f"Total de {len(linhas)} notícias encontradas para envio ao Supabase.")

    if "seu-projeto" in SUPABASE_URL:
        print("\n[Aviso] Configure as variáveis SUPABASE_URL e SUPABASE_KEY para envio real à nuvem.")
        print("Modo de teste: 10 primeiras notícias lidas com sucesso no SQLite.")
        for r in linhas[:3]:
            print(f"  - [{r['portal']}] {r['titulo'][:60]}...")
        return

    endpoint = f"{SUPABASE_URL}/rest/v1/noticias"
    lote = []
    enviados = 0

    for r in linhas:
        lote.append({
            "sqlite_id": r["id"],
            "veiculo": r["portal"],
            "titulo": r["titulo"],
            "url": r["url_final"] or r["link_original"],
            "data_publicacao": r["publicado_em"] or r["dia_busca"],
            "conteudo_resumo": (r["texto"] or "")[:300],
            "n_palavras": r["n_palavras"] or 0,
            "opiniao": bool(r["opiniao"]),
        })

        if len(lote) >= 50:
            resp = requests.post(endpoint, headers=HEADERS, data=json.dumps(lote))
            if resp.status_code in (200, 201):
                enviados += len(lote)
                print(f"Enviadas: {enviados}/{len(linhas)}...")
            else:
                print(f"Erro no lote: {resp.text[:200]}")
            lote = []

    if lote:
        resp = requests.post(endpoint, headers=HEADERS, data=json.dumps(lote))
        if resp.status_code in (200, 201):
            enviados += len(lote)
            print(f"Envio concluído: {enviados}/{len(linhas)} salvas no Supabase.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="noticias.db", help="Caminho do noticias.db")
    args = parser.parse_args()
    migrar(args.db)
