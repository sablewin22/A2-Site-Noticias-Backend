# -*- coding: utf-8 -*-
"""
Comparação Cruzada Completa: Gemini Flash Lite vs Groq Qwen vs Llama 3.2 vs Anotação Humana (Jamilly)
Calcula porcentagens de acerto, Cohen's Kappa e matrizes de concordância entre os 3 modelos e o humano.
Uso: python comparar_3_modelos.py
"""

import os
import csv
import sqlite3
import numpy as np
from collections import Counter
from sklearn.metrics import cohen_kappa_score
from classificar_noticias import DIMENSOES

def carregar_dados():
    con = sqlite3.connect("noticias.db")
    con.row_factory = sqlite3.Row
    
    # 1. Carregar anotações de Jamilly
    arq_jamilly = "validacao_jamilly.csv"
    if not os.path.exists(arq_jamilly):
        raise SystemExit(f"Arquivo {arq_jamilly} não encontrado.")
    
    with open(arq_jamilly, encoding="utf-8-sig") as f:
        linhas_jamilly = list(csv.DictReader(f, delimiter=";"))
    
    dados = {}
    for l in linhas_jamilly:
        nid = int(l["noticia_id"])
        dados[nid] = {
            "noticia_id": nid,
            "portal": l.get("portal", ""),
            "titulo": l.get("titulo", ""),
            "humano": {dim: (l.get(f"humano_{dim}") or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
        }
    
    # 2. Carregar Gemini
    for row in con.execute("SELECT * FROM classificacoes WHERE noticia_id IN (" + ",".join(str(k) for k in dados) + ")"):
        nid = row["noticia_id"]
        if nid in dados:
            dados[nid]["gemini"] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
            
    # 3. Carregar Groq
    for row in con.execute("SELECT * FROM classificacoes_groq WHERE noticia_id IN (" + ",".join(str(k) for k in dados) + ")"):
        nid = row["noticia_id"]
        if nid in dados:
            dados[nid]["groq"] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
            
    # 4. Carregar Llama
    for row in con.execute("SELECT * FROM classificacoes_llama WHERE noticia_id IN (" + ",".join(str(k) for k in dados) + ")"):
        nid = row["noticia_id"]
        if nid in dados:
            dados[nid]["llama"] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
            
    return dados

def calcular_metricas(pares):
    if not pares:
        return 0.0, 0.0, 0
    validos = [(a, b) for a, b in pares if a and b]
    if not validos:
        return 0.0, 0.0, 0
    acertos = sum(a == b for a, b in validos)
    pct = (acertos / len(validos)) * 100.0
    y1 = [a for a, b in validos]
    y2 = [b for a, b in validos]
    try:
        kappa = cohen_kappa_score(y1, y2)
        if np.isnan(kappa):
            kappa = 0.0
    except Exception:
        kappa = 0.0
    return pct, kappa, len(validos)

def main():
    dados = carregar_dados()
    ids = sorted(dados.keys())
    print(f"Total de notícias analisadas: {len(ids)}\n")
    
    dimensoes = ["tipo", "tom_manchete", "profundidade", "postura", "vies_politico", "tema"]
    
    print("=" * 100)
    print("TABELA 1: ACERTO DOS MODELOS EM RELAÇÃO À ANOTAÇÃO HUMANA (JAMILLY)")
    print("=" * 100)
    print(f"{'Dimensão':<18} | {'Gemini vs Jamilly':<22} | {'Groq vs Jamilly':<22} | {'Llama 3.2 vs Jamilly':<22}")
    print("-" * 100)
    
    totais = {"gemini": [], "groq": [], "llama": []}
    
    for dim in dimensoes:
        pares_gemini = [(dados[i]["humano"].get(dim), dados[i].get("gemini", {}).get(dim)) for i in ids]
        pares_groq   = [(dados[i]["humano"].get(dim), dados[i].get("groq", {}).get(dim)) for i in ids]
        pares_llama  = [(dados[i]["humano"].get(dim), dados[i].get("llama", {}).get(dim)) for i in ids]
        
        pct_gem, k_gem, n_gem = calcular_metricas(pares_gemini)
        pct_groq, k_groq, n_groq = calcular_metricas(pares_groq)
        pct_lla, k_lla, n_lla = calcular_metricas(pares_llama)
        
        totais["gemini"].extend([a == b for a, b in pares_gemini if a and b])
        totais["groq"].extend([a == b for a, b in pares_groq if a and b])
        totais["llama"].extend([a == b for a, b in pares_llama if a and b])
        
        nome_dim = DIMENSOES[dim]["nome"]
        print(f"{nome_dim:<18} | {pct_gem:>5.1f}% (k={k_gem:>.3f})     | {pct_groq:>5.1f}% (k={k_groq:>.3f})     | {pct_lla:>5.1f}% (k={k_lla:>.3f})")
    
    print("-" * 100)
    media_gem = (sum(totais["gemini"]) / len(totais["gemini"]) * 100) if totais["gemini"] else 0
    media_groq = (sum(totais["groq"]) / len(totais["groq"]) * 100) if totais["groq"] else 0
    media_lla = (sum(totais["llama"]) / len(totais["llama"]) * 100) if totais["llama"] else 0
    print(f"{'MÉDIA GERAL':<18} | {media_gem:>5.1f}%                 | {media_groq:>5.1f}%                 | {media_lla:>5.1f}%")
    print("=" * 100)
    print()
    
    print("=" * 100)
    print("TABELA 2: CONCORDÂNCIA DIRETA ENTRE OS MODELOS DE INTELIGÊNCIA ARTIFICIAL")
    print("=" * 100)
    print(f"{'Dimensão':<18} | {'Gemini vs Groq':<22} | {'Gemini vs Llama 3.2':<22} | {'Groq vs Llama 3.2':<22}")
    print("-" * 100)
    
    tot_inter = {"gem_groq": [], "gem_lla": [], "groq_lla": []}
    for dim in dimensoes:
        p_gg = [(dados[i].get("gemini", {}).get(dim), dados[i].get("groq", {}).get(dim)) for i in ids]
        p_gl = [(dados[i].get("gemini", {}).get(dim), dados[i].get("llama", {}).get(dim)) for i in ids]
        p_ql = [(dados[i].get("groq", {}).get(dim), dados[i].get("llama", {}).get(dim)) for i in ids]
        
        pct_gg, k_gg, _ = calcular_metricas(p_gg)
        pct_gl, k_gl, _ = calcular_metricas(p_gl)
        pct_ql, k_ql, _ = calcular_metricas(p_ql)
        
        tot_inter["gem_groq"].extend([a == b for a, b in p_gg if a and b])
        tot_inter["gem_lla"].extend([a == b for a, b in p_gl if a and b])
        tot_inter["groq_lla"].extend([a == b for a, b in p_ql if a and b])
        
        nome_dim = DIMENSOES[dim]["nome"]
        print(f"{nome_dim:<18} | {pct_gg:>5.1f}% (k={k_gg:>.3f})     | {pct_gl:>5.1f}% (k={k_gl:>.3f})     | {pct_ql:>5.1f}% (k={k_ql:>.3f})")
        
    print("-" * 100)
    m_gg = (sum(tot_inter["gem_groq"]) / len(tot_inter["gem_groq"]) * 100) if tot_inter["gem_groq"] else 0
    m_gl = (sum(tot_inter["gem_lla"]) / len(tot_inter["gem_lla"]) * 100) if tot_inter["gem_lla"] else 0
    m_ql = (sum(tot_inter["groq_lla"]) / len(tot_inter["groq_lla"]) * 100) if tot_inter["groq_lla"] else 0
    print(f"{'MÉDIA INTER-MODELOS':<18} | {m_gg:>5.1f}%                 | {m_gl:>5.1f}%                 | {m_ql:>5.1f}%")
    print("=" * 100)

if __name__ == "__main__":
    main()
