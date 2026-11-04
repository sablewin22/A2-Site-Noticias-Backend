# -*- coding: utf-8 -*-
"""
Script de Migração Completa: SQLite (noticias.db) -> Supabase (Nuvem)
Migra: grupos -> acontecimentos, noticias + textos -> noticias
Execução: python migrar_para_supabase.py --db noticias.db
"""

import os
import sqlite3
import requests
import json
import argparse
from datetime import datetime

# Carregar variáveis do arquivo .env se existirem
ARQ_ENV = ".env"
if os.path.exists(ARQ_ENV):
    with open(ARQ_ENV, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def migrar(banco_sqlite):
    if not os.path.exists(banco_sqlite):
        print(f"Erro: Arquivo '{banco_sqlite}' não encontrado.")
        print("Coloque o noticias.db recebido da Sabrina na pasta do projeto e tente novamente.")
        return

    if not SUPABASE_URL or not SUPABASE_KEY or "seu-projeto" in SUPABASE_URL:
        print("Erro: SUPABASE_URL ou SUPABASE_KEY não configuradas no .env.")
        return

    con = sqlite3.connect(banco_sqlite)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print("Conectado ao SQLite local. Iniciando migração para o Supabase...")

    # 1. Migrar acontecimentos (tabela 'grupos' do SQLite)
    tem_grupos = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='grupos'").fetchone()
    if tem_grupos:
        print("\n--- Migrando Acontecimentos / Grupos ---")
        sql_grupos = """
            SELECT g.id, g.n_portais, g.n_noticias,
                   COALESCE(g.titulo, 'Acontecimento ' || g.id) AS titulo,
                   COALESCE(gt.tema, 'geral') AS tema_final
            FROM grupos g
            LEFT JOIN grupo_tema gt ON gt.grupo_id = g.id
        """
        grupos = cur.execute(sql_grupos).fetchall()

        endpoint_acontecimentos = f"{SUPABASE_URL}/rest/v1/acontecimentos"
        lote_g = []
        for g in grupos:
            lote_g.append({
                "id": g["id"],
                "tema": g["tema_final"] or "geral",
                "titulo": g["titulo"] or f"Acontecimento {g['id']}",
                "n_portais": g["n_portais"] or 1,
                "total_noticias": g["n_noticias"] or 1
            })
            if len(lote_g) >= 100:
                requests.post(endpoint_acontecimentos, headers=HEADERS, data=json.dumps(lote_g))
                lote_g = []
        if lote_g:
            requests.post(endpoint_acontecimentos, headers=HEADERS, data=json.dumps(lote_g))
        print(f"Total de {len(grupos)} acontecimentos sincronizados no Supabase.")

    # 2. Migrar notícias
    print("\n--- Migrando Notícias ---")
    sql_noticias = """
        SELECT n.id, n.portal, n.titulo, n.link_original, n.publicado_em, n.dia_busca,
               t.url_final, t.texto, t.n_palavras, t.imagem_url AS imagem, t.provavel_opiniao AS opiniao,
               ng.grupo_id,
               c.tipo, c.tipo_confianca,
               c.tom_manchete, c.tom_manchete_confianca,
               c.profundidade, c.profundidade_confianca,
               c.postura, c.postura_confianca,
               c.vies_politico, c.vies_politico_confianca,
               c.tema, c.tema_confianca,
               c.justificativa
        FROM noticias n
        LEFT JOIN textos t ON t.noticia_id = n.id
        LEFT JOIN noticia_grupo ng ON ng.noticia_id = n.id
        LEFT JOIN classificacoes c ON c.noticia_id = n.id
    """
    try:
        noticias = cur.execute(sql_noticias).fetchall()
    except Exception as e:
        # Fallback sem classificações
        sql_fallback = """
            SELECT n.id, n.portal, n.titulo, n.link_original, n.publicado_em, n.dia_busca,
                   t.url_final, t.texto, t.n_palavras, t.imagem_url AS imagem, t.provavel_opiniao AS opiniao,
                   ng.grupo_id,
                   NULL AS tipo, NULL AS tipo_confianca,
                   NULL AS tom_manchete, NULL AS tom_manchete_confianca,
                   NULL AS profundidade, NULL AS profundidade_confianca,
                   NULL AS postura, NULL AS postura_confianca,
                   NULL AS vies_politico, NULL AS vies_politico_confianca,
                   NULL AS tema, NULL AS tema_confianca,
                   NULL AS justificativa
            FROM noticias n
            LEFT JOIN textos t ON t.noticia_id = n.id
            LEFT JOIN noticia_grupo ng ON ng.noticia_id = n.id
        """
        noticias = cur.execute(sql_fallback).fetchall()

    print(f"Encontradas {len(noticias)} notícias no SQLite. Enviando para o Supabase...")
    endpoint_noticias = f"{SUPABASE_URL}/rest/v1/noticias"
    lote_n = []
    enviados = 0

    for r in noticias:
        url_materia = r["url_final"] or r["link_original"] or f"sem-url-{r['id']}"
        raw_text = r["texto"] or ""
        trecho_curto = raw_text[:200] if raw_text else (r["titulo"] or "")[:200]

        pub_data = r["publicado_em"] or r["dia_busca"]
        if pub_data and len(pub_data) == 10:
            pub_data += "T12:00:00Z"

        lote_n.append({
            "id": r["id"],
            "acontecimento_id": r["grupo_id"],
            "portal": r["portal"],
            "titulo": r["titulo"],
            "url": url_materia,
            "publicado_em": pub_data,
            "trecho": trecho_curto,
            "texto": raw_text[:3000] if raw_text else None, # guarda texto resumido para consulta
            "imagem": r["imagem"],
            "opiniao": bool(r["opiniao"]),
            "tipo": r["tipo"],
            "tipo_confianca": r["tipo_confianca"],
            "tom_manchete": r["tom_manchete"],
            "tom_manchete_confianca": r["tom_manchete_confianca"],
            "profundidade": r["profundidade"],
            "profundidade_confianca": r["profundidade_confianca"],
            "postura": r["postura"],
            "postura_confianca": r["postura_confianca"],
            "vies_politico": r["vies_politico"],
            "vies_politico_confianca": r["vies_politico_confianca"],
            "tema": r["tema"],
            "tema_confianca": r["tema_confianca"],
            "justificativa": r["justificativa"]
        })

        if len(lote_n) >= 50:
            resp = requests.post(endpoint_noticias, headers=HEADERS, data=json.dumps(lote_n))
            if resp.status_code in (200, 201):
                enviados += len(lote_n)
                print(f"Progresso: {enviados}/{len(noticias)} notícias salvas...")
            else:
                print(f"Aviso no lote: {resp.text[:200]}")
            lote_n = []

    if lote_n:
        resp = requests.post(endpoint_noticias, headers=HEADERS, data=json.dumps(lote_n))
        if resp.status_code in (200, 201):
            enviados += len(lote_n)

    print(f"\nMigração concluída com sucesso! Total de {enviados} notícias sincronizadas no Supabase.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="noticias.db", help="Caminho do noticias.db")
    args = parser.parse_args()
    migrar(args.db)
