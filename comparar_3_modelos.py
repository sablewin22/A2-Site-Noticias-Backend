# -*- coding: utf-8 -*-
"""
Comparação Cruzada Completa: Gemini Flash Lite vs Groq Qwen vs Llama 3.2 vs Gabarito da Equipe Humana
Calcula porcentagens de acerto, Cohen's Kappa e matrizes de concordância inter-modelos e inter-humanos.
Gabarito humano construído por MAIORIA ESTRITA entre os 4 integrantes (Jamilly, Sabrina, Júlia e João).
Empates no topo (como 2-2 ou 1-1-1-1) são desconsiderados por ambiguidade genuína sem consenso.
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
    
    # 1. Carregar validações dos 4 integrantes humanos
    arquivos_humanos = {
        "jamilly": "validacao_jamilly.csv",
        "sabrina": "validacao_sabrina.csv",
        "julia": "validacao_julia.csv",
        "joao": "validacao_joao.csv"
    }
    
    votos_humanos = {}
    for nome, arq in arquivos_humanos.items():
        if not os.path.exists(arq):
            raise SystemExit(f"Arquivo {arq} não encontrado.")
        with open(arq, encoding="utf-8-sig") as f:
            amostra = f.read(2048)
            f.seek(0)
            delimitador = ";" if ";" in amostra else ","
            linhas = list(csv.DictReader(f, delimiter=delimitador))
            for l in linhas:
                nid = int(l["noticia_id"])
                if nid not in votos_humanos:
                    votos_humanos[nid] = {
                        "noticia_id": nid,
                        "portal": l.get("portal", ""),
                        "titulo": l.get("titulo", ""),
                        "avaliadores": {}
                    }
                votos_humanos[nid]["avaliadores"][nome] = {
                    dim: (l.get(f"humano_{dim}") or "").strip().lower().replace(" ", "_")
                    for dim in DIMENSOES
                }

    # 2. Construir Gabarito por MAIORIA ESTRITA entre os 4 integrantes
    # Exige pluralidade clara sem empate no topo. Empates (ex: 2 a 2 ou 1-1-1-1) viram None.
    gabarito_maioria = {}
    casos_sem_consenso = []
    
    for nid, d in votos_humanos.items():
        gabarito_maioria[nid] = {}
        for dim in DIMENSOES:
            votos = [d["avaliadores"][nome].get(dim) for nome in arquivos_humanos if d["avaliadores"][nome].get(dim)]
            if not votos:
                gabarito_maioria[nid][dim] = None
            else:
                contagem = Counter(votos)
                mais_comum, qtd = contagem.most_common(1)[0]
                # Se houver empate no topo (ex: 2 a 2), desconsidera por ambiguidade
                if len(contagem) > 1 and contagem.most_common(2)[1][1] == qtd:
                    gabarito_maioria[nid][dim] = None
                    casos_sem_consenso.append((nid, dim, votos))
                elif qtd >= 2:
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
    
    print("=" * 125)
    print("RELATÓRIO METODOLÓGICO: BENCHMARK DOS MODELOS DE IA FRENTE AO GABARITO HUMANO DA EQUIPE COMPLETA")
    print("=" * 125)
    print(f"Total de notícias analisadas: {len(ids)} | Avaliadores: Jamilly, Sabrina, Júlia e João")
    print(f"Ambiguidade sem consenso estrito (desconsiderada): {len(sem_consenso)} casos\n")
    
    # ------------------------------------------------------------- TABELA 1: GABARITO MAIORIA
    print("=" * 125)
    print("TABELA 1: ACERTO DOS MODELOS EM RELAÇÃO AO GABARITO DE MAIORIA HUMANA (4 INTEGRANTES)")
    print("=" * 125)
    print(f"{'Dimensão':<20} | {'Gemini vs Maioria':<26} | {'Groq vs Maioria':<26} | {'Llama 3.2 vs Maioria':<26} | {'Consenso Humano':<16}")
    print("-" * 125)
    
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
        print(f"{nome_dim:<20} | {pct_gem:>5.1f}% (k={k_gem:>.3f})             | {pct_grq:>5.1f}% (k={k_grq:>.3f})             | {pct_lla:>5.1f}% (k={k_lla:>.3f})             | {n_gem}/{total_esperado} com consenso")
        
    print("-" * 125)
    m_gem = (sum(totais["gemini"]) / len(totais["gemini"]) * 100) if totais["gemini"] else 0
    m_grq = (sum(totais["groq"]) / len(totais["groq"]) * 100) if totais["groq"] else 0
    m_lla = (sum(totais["llama"]) / len(totais["llama"]) * 100) if totais["llama"] else 0
    print(f"{'MÉDIA GERAL (ACERTO)':<20} | {m_gem:>5.1f}%                      | {m_grq:>5.1f}%                      | {m_lla:>5.1f}%                      | {len(totais['gemini'])} pares avaliados")
    print("=" * 125)
    print()

    # ------------------------------------------------------------- TABELA 2: INTER-MODELOS
    print("=" * 125)
    print("TABELA 2: CONCORDÂNCIA DIRETA ENTRE OS MODELOS DE INTELIGÊNCIA ARTIFICIAL")
    print("=" * 125)
    print(f"{'Dimensão':<20} | {'Gemini vs Groq':<26} | {'Gemini vs Llama 3.2':<26} | {'Groq vs Llama 3.2':<26}")
    print("-" * 125)
    
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
        print(f"{nome_dim:<20} | {pct_gg:>5.1f}% (k={k_gg:>.3f})             | {pct_gl:>5.1f}% (k={k_gl:>.3f})             | {pct_ql:>5.1f}% (k={k_ql:>.3f})")
        
    print("-" * 125)
    m_gg = (sum(tot_inter["gem_groq"]) / len(tot_inter["gem_groq"]) * 100) if tot_inter["gem_groq"] else 0
    m_gl = (sum(tot_inter["gem_lla"]) / len(tot_inter["gem_lla"]) * 100) if tot_inter["gem_lla"] else 0
    m_ql = (sum(tot_inter["groq_lla"]) / len(tot_inter["groq_lla"]) * 100) if tot_inter["groq_lla"] else 0
    print(f"{'MÉDIA INTER-MODELOS':<20} | {m_gg:>5.1f}%                      | {m_gl:>5.1f}%                      | {m_ql:>5.1f}%")
    print("=" * 125)
    print()

    # ------------------------------------------------------------- TABELA 3: INTER-HUMANOS (4 INTEGRANTES - 6 PARES)
    print("=" * 125)
    print("TABELA 3: MATRIZ DE CONCORDÂNCIA INTERANOTADORES ENTRE OS 4 INTEGRANTES HUMANOS (KAPPA DE COHEN)")
    print("=" * 125)
    pares_nomes = [
        ("jamilly", "sabrina", "Jam vs Sab"),
        ("jamilly", "julia", "Jam vs Jul"),
        ("jamilly", "joao", "Jam vs João"),
        ("sabrina", "julia", "Sab vs Jul"),
        ("sabrina", "joao", "Sab vs João"),
        ("julia", "joao", "Jul vs João")
    ]
    
    cabecalho = f"{'Dimensão':<18} | " + " | ".join(f"{rotulo:<15}" for _, _, rotulo in pares_nomes) + f" | {'Média Geral':<12}"
    print(cabecalho)
    print("-" * 125)
    
    medias_dim = []
    totais_pares = {p[2]: [] for p in pares_nomes}
    
    for dim in dimensoes:
        linha_str = f"{DIMENSOES[dim]['nome']:<18} | "
        kappas_dim = []
        for u1, u2, rotulo in pares_nomes:
            p = [(votos_humanos[i]["avaliadores"][u1].get(dim), votos_humanos[i]["avaliadores"][u2].get(dim)) for i in ids]
            pct, k, _ = calcular_metricas(p)
            linha_str += f"{pct:>4.0f}% (k={k:>.2f})  | "
            kappas_dim.append(k)
            totais_pares[rotulo].extend([a == b for a, b in p if a and b and a != "nan" and b != "nan"])
        media_k = sum(kappas_dim) / len(kappas_dim) if kappas_dim else 0
        medias_dim.append(media_k)
        linha_str += f"k={media_k:>.3f}"
        print(linha_str)
        
    print("-" * 125)
    linha_rodape = f"{'MÉDIA POR PAR':<18} | "
    todas_medias = []
    for _, _, rotulo in pares_nomes:
        val = totais_pares[rotulo]
        m = (sum(val) / len(val) * 100) if val else 0
        linha_rodape += f"{m:>5.1f}%          | "
        todas_medias.append(m)
    m_humana_total = sum(todas_medias) / len(todas_medias) if todas_medias else 0
    linha_rodape += f"{m_humana_total:>5.1f}%"
    print(linha_rodape)
    print("=" * 125)

if __name__ == "__main__":
    main()
