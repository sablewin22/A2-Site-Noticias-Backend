# -*- coding: utf-8 -*-
"""
Comparação Cruzada Completa: Gemini Flash Lite vs Groq Qwen vs Llama 3.2 vs Gabarito da Equipe Humana
Calcula porcentagens de acerto, Cohen's Kappa e matrizes de concordância inter-modelos e inter-humanos.
Gabarito humano construído por MAIORIA ESTRITA (mínimo 2 votos; empates triplos 1-1-1 são desconsiderados por ambiguidade).
Uso: python comparar_3_modelos.py
"""

import os
import csv
import sqlite3
import warnings
import numpy as np
from collections import Counter
from sklearn.metrics import cohen_kappa_score
from classificar_noticias import DIMENSOES

warnings.filterwarnings("ignore")

def carregar_dados():
    con = sqlite3.connect("noticias.db")
    con.row_factory = sqlite3.Row
    
    # 1. Carregar validações das 3 integrantes humanas
    arquivos_humanos = {
        "jamilly": "validacao_jamilly.csv",
        "sabrina": "validacao_sabrina.csv",
        "julia": "validacao_julia.csv"
    }
    
    votos_humanos = {}
    for nome, arq in arquivos_humanos.items():
        if not os.path.exists(arq):
            raise SystemExit(f"Arquivo {arq} não encontrado.")
        with open(arq, encoding="utf-8-sig") as f:
            linhas = list(csv.DictReader(f, delimiter=";"))
            for l in linhas:
                nid = int(l["noticia_id"])
                if nid not in votos_humanos:
                    votos_humanos[nid] = {
                        "noticia_id": nid,
                        "portal": l.get("portal", ""),
                        "titulo": l.get("titulo", ""),
                        "avaliadoras": {}
                    }
                votos_humanos[nid]["avaliadoras"][nome] = {
                    dim: (l.get(f"humano_{dim}") or "").strip().lower().replace(" ", "_")
                    for dim in DIMENSOES
                }

    # 2. Construir Gabarito por MAIORIA ESTRITA (sem privilégio ou voto de desempate)
    # Exige no mínimo 2 votos concordantes. Divisão 1-1-1 vira None (sem consenso).
    gabarito_maioria = {}
    casos_sem_consenso = []
    
    for nid, d in votos_humanos.items():
        gabarito_maioria[nid] = {}
        for dim in DIMENSOES:
            votos = [d["avaliadoras"][nome].get(dim) for nome in arquivos_humanos if d["avaliadoras"][nome].get(dim)]
            if not votos:
                gabarito_maioria[nid][dim] = None
            else:
                contagem = Counter(votos)
                mais_comum, qtd = contagem.most_common(1)[0]
                if qtd >= 2:
                    gabarito_maioria[nid][dim] = mais_comum
                else:
                    gabarito_maioria[nid][dim] = None
                    casos_sem_consenso.append((nid, dim, votos))

    # 3. Carregar modelos de IA do banco noticias.db
    ids_str = ",".join(str(k) for k in votos_humanos)
    
    # Gemini
    gemini_data = {}
    for row in con.execute(f"SELECT * FROM classificacoes WHERE noticia_id IN ({ids_str}) AND modelo LIKE '%gemini%'"):
        nid = row["noticia_id"]
        gemini_data[nid] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
        
    # Groq Qwen
    groq_data = {}
    for row in con.execute(f"SELECT * FROM classificacoes_groq WHERE noticia_id IN ({ids_str})"):
        nid = row["noticia_id"]
        groq_data[nid] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}
        
    # Ollama Llama 3.2
    llama_data = {}
    for row in con.execute(f"SELECT * FROM classificacoes_llama WHERE noticia_id IN ({ids_str})"):
        nid = row["noticia_id"]
        llama_data[nid] = {dim: (row[dim] or "").strip().lower().replace(" ", "_") for dim in DIMENSOES}

    return votos_humanos, gabarito_maioria, gemini_data, groq_data, llama_data, casos_sem_consenso

def calcular_metricas(pares):
    if not pares:
        return 0.0, 0.0, 0
    validos = [(a, b) for a, b in pares if a and b and a != "nan" and b != "nan"]
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
    votos_humanos, gabarito, gemini, groq, llama, sem_consenso = carregar_dados()
    ids = sorted(votos_humanos.keys())
    
    dimensoes = ["tipo", "tom_manchete", "profundidade", "postura", "vies_politico", "tema"]
    
    print("=" * 115)
    print("RELATÓRIO METODOLÓGICO: BENCHMARK DOS MODELOS DE IA FRENTE AO GABARITO HUMANO DA EQUIPE")
    print("=" * 115)
    print(f"Total de notícias analisadas: {len(ids)} | Ambiguidade tripla (1-1-1 desconsiderada): {len(sem_consenso)} casos\n")
    
    # ------------------------------------------------------------- TABELA 1: GABARITO MAIORIA
    print("=" * 115)
    print("TABELA 1: ACERTO DOS MODELOS EM RELAÇÃO AO GABARITO DA MAIORIA HUMANA (MESMO PESO PARA TODAS)")
    print("=" * 115)
    print(f"{'Dimensão':<20} | {'Gemini vs Maioria':<24} | {'Groq vs Maioria':<24} | {'Llama 3.2 vs Maioria':<24} | {'Consenso Humano':<16}")
    print("-" * 115)
    
    totais = {"gemini": [], "groq": [], "llama": []}
    for dim in dimensoes:
        p_gem = [(gabarito[i].get(dim), gemini.get(i, {}).get(dim)) for i in ids]
        p_grq = [(gabarito[i].get(dim), groq.get(i, {}).get(dim)) for i in ids]
        p_lla = [(gabarito[i].get(dim), llama.get(i, {}).get(dim)) for i in ids]
        
        pct_gem, k_gem, n_gem = calcular_metricas(p_gem)
        pct_grq, k_grq, n_grq = calcular_metricas(p_grq)
        pct_lla, k_lla, n_lla = calcular_metricas(p_lla)
        
        totais["gemini"].extend([a == b for a, b in p_gem if a and b and a != "nan" and b != "nan"])
        totais["groq"].extend([a == b for a, b in p_grq if a and b and a != "nan" and b != "nan"])
        totais["llama"].extend([a == b for a, b in p_lla if a and b and a != "nan" and b != "nan"])
        
        nome_dim = DIMENSOES[dim]["nome"]
        total_esperado = 30 if dim in ["tema", "tom_manchete"] else 26
        print(f"{nome_dim:<20} | {pct_gem:>5.1f}% (k={k_gem:>.3f})           | {pct_grq:>5.1f}% (k={k_grq:>.3f})           | {pct_lla:>5.1f}% (k={k_lla:>.3f})           | {n_gem}/{total_esperado} com maioria")
        
    print("-" * 115)
    m_gem = (sum(totais["gemini"]) / len(totais["gemini"]) * 100) if totais["gemini"] else 0
    m_grq = (sum(totais["groq"]) / len(totais["groq"]) * 100) if totais["groq"] else 0
    m_lla = (sum(totais["llama"]) / len(totais["llama"]) * 100) if totais["llama"] else 0
    print(f"{'MÉDIA GERAL (ACERTO)':<20} | {m_gem:>5.1f}%                    | {m_grq:>5.1f}%                    | {m_lla:>5.1f}%                    | {len(totais['gemini'])} pares avaliados")
    print("=" * 115)
    print()

    # ------------------------------------------------------------- TABELA 2: INTER-MODELOS
    print("=" * 115)
    print("TABELA 2: CONCORDÂNCIA DIRETA ENTRE OS MODELOS DE INTELIGÊNCIA ARTIFICIAL")
    print("=" * 115)
    print(f"{'Dimensão':<20} | {'Gemini vs Groq':<24} | {'Gemini vs Llama 3.2':<24} | {'Groq vs Llama 3.2':<24}")
    print("-" * 115)
    
    tot_inter = {"gem_groq": [], "gem_lla": [], "groq_lla": []}
    for dim in dimensoes:
        p_gg = [(gemini.get(i, {}).get(dim), groq.get(i, {}).get(dim)) for i in ids]
        p_gl = [(gemini.get(i, {}).get(dim), llama.get(i, {}).get(dim)) for i in ids]
        p_ql = [(groq.get(i, {}).get(dim), llama.get(i, {}).get(dim)) for i in ids]
        
        pct_gg, k_gg, _ = calcular_metricas(p_gg)
        pct_gl, k_gl, _ = calcular_metricas(p_gl)
        pct_ql, k_ql, _ = calcular_metricas(p_ql)
        
        tot_inter["gem_groq"].extend([a == b for a, b in p_gg if a and b and a != "nan" and b != "nan"])
        tot_inter["gem_lla"].extend([a == b for a, b in p_gl if a and b and a != "nan" and b != "nan"])
        tot_inter["groq_lla"].extend([a == b for a, b in p_ql if a and b and a != "nan" and b != "nan"])
        
        nome_dim = DIMENSOES[dim]["nome"]
        print(f"{nome_dim:<20} | {pct_gg:>5.1f}% (k={k_gg:>.3f})           | {pct_gl:>5.1f}% (k={k_gl:>.3f})           | {pct_ql:>5.1f}% (k={k_ql:>.3f})")
        
    print("-" * 115)
    m_gg = (sum(tot_inter["gem_groq"]) / len(tot_inter["gem_groq"]) * 100) if tot_inter["gem_groq"] else 0
    m_gl = (sum(tot_inter["gem_lla"]) / len(tot_inter["gem_lla"]) * 100) if tot_inter["gem_lla"] else 0
    m_ql = (sum(tot_inter["groq_lla"]) / len(tot_inter["groq_lla"]) * 100) if tot_inter["groq_lla"] else 0
    print(f"{'MÉDIA INTER-MODELOS':<20} | {m_gg:>5.1f}%                    | {m_gl:>5.1f}%                    | {m_ql:>5.1f}%")
    print("=" * 115)
    print()

    # ------------------------------------------------------------- TABELA 3: INTER-HUMANOS
    print("=" * 115)
    print("TABELA 3: CONCORDÂNCIA INTERANOTADORES ENTRE AS AVALIADORAS HUMANAS")
    print("=" * 115)
    print(f"{'Dimensão':<20} | {'Jamilly vs Sabrina':<24} | {'Jamilly vs Júlia':<24} | {'Sabrina vs Júlia':<24}")
    print("-" * 115)
    
    for dim in dimensoes:
        p_js = [(votos_humanos[i]["avaliadoras"]["jamilly"].get(dim), votos_humanos[i]["avaliadoras"]["sabrina"].get(dim)) for i in ids]
        p_ju = [(votos_humanos[i]["avaliadoras"]["jamilly"].get(dim), votos_humanos[i]["avaliadoras"]["julia"].get(dim)) for i in ids]
        p_su = [(votos_humanos[i]["avaliadoras"]["sabrina"].get(dim), votos_humanos[i]["avaliadoras"]["julia"].get(dim)) for i in ids]
        
        pct_js, k_js, _ = calcular_metricas(p_js)
        pct_ju, k_ju, _ = calcular_metricas(p_ju)
        pct_su, k_su, _ = calcular_metricas(p_su)
        
        nome_dim = DIMENSOES[dim]["nome"]
        print(f"{nome_dim:<20} | {pct_js:>5.1f}% (k={k_js:>.3f})           | {pct_ju:>5.1f}% (k={k_ju:>.3f})           | {pct_su:>5.1f}% (k={k_su:>.3f})")
    print("=" * 115)

if __name__ == "__main__":
    main()
