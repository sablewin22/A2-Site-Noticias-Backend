# -*- coding: utf-8 -*-
"""
Classificação de Notícias com Llama 3.2 (Meta) via Ollama Local.
Executa sobre a amostra das 30 notícias para benchmark comparativo com Gemini e Groq (Qwen).
Uso: python classificar_llama.py
"""

import os
import csv
import json
import time
import sqlite3
import requests
from classificar_noticias import DIMENSOES, MAX_CARACTERES, montar_prompt

MODELO_LLAMA = "llama3.2"
URL_OLLAMA = "http://localhost:11434/api/generate"

def obter_30_noticias(con, arq_validacao="validacao.csv"):
    if not os.path.exists(arq_validacao):
        raise SystemExit(f"Arquivo {arq_validacao} não encontrado.")
    with open(arq_validacao, encoding="utf-8-sig") as f:
        r = csv.DictReader(f, delimiter=";")
        ids = [int(linha["noticia_id"]) for linha in r]
    
    placeholders = ",".join("?" for _ in ids)
    sql = f"""
        SELECT n.id, n.portal, n.titulo, t.texto, t.n_palavras
        FROM noticias n
        JOIN textos t ON t.noticia_id = n.id
        WHERE n.id IN ({placeholders})
    """
    linhas = con.execute(sql, ids).fetchall()
    ordem_map = {id_: idx for idx, id_ in enumerate(ids)}
    linhas.sort(key=lambda x: ordem_map.get(x[0], 999))
    return linhas

def preparar_tabela_llama(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS classificacoes_llama (
            noticia_id INTEGER PRIMARY KEY,
            tipo TEXT,
            tom_manchete TEXT,
            profundidade TEXT,
            postura TEXT,
            vies_politico TEXT,
            tema TEXT,
            justificativa TEXT,
            modelo TEXT,
            classificado_em TEXT
        )
    """)
    con.commit()

def normalizar_rotulo(dimensao, valor):
    if not valor:
        return ""
    v = str(valor).strip().lower().replace(" ", "_")
    for rotulo in DIMENSOES[dimensao]["criterios"]:
        if v == rotulo or rotulo in v:
            return rotulo
    return v

def classificar_uma_llama(titulo, texto, n_palavras):
    so_manchete = (n_palavras or 0) < 80 or not texto
    prompt = montar_prompt(titulo, texto, so_manchete)
    instrucao_json = (
        "\n\nResponda ESTRITAMENTE em formato JSON com as seguintes chaves:\n"
        "tipo, tom_manchete, profundidade, postura, vies_politico, tema, justificativa.\n"
        "Cada chave categórica DEVE usar exclusivamente um dos rótulos definidos na descrição."
    )
    prompt_completo = prompt + instrucao_json
    
    body = {
        "model": MODELO_LLAMA,
        "prompt": prompt_completo,
        "format": "json",
        "stream": False
    }
    
    for tentativa in range(1, 6):
        try:
            r = requests.post(URL_OLLAMA, json=body, timeout=120)
            if r.status_code == 200:
                raw_text = r.json().get("response", "{}")
                dados = json.loads(raw_text)
                # normalizar valores para os rótulos válidos
                res_norm = {}
                for dim in ["tipo", "tom_manchete", "profundidade", "postura", "vies_politico", "tema"]:
                    res_norm[dim] = normalizar_rotulo(dim, dados.get(dim, ""))
                res_norm["justificativa"] = str(dados.get("justificativa", ""))[:500]
                return res_norm
            else:
                print(f" [HTTP {r.status_code}] tentativa {tentativa}")
                time.sleep(10)
        except Exception as e:
            print(f" [Erro: {e}] tentativa {tentativa}")
            time.sleep(10)
    raise RuntimeError("Falha no Ollama após 5 tentativas.")

def main():
    con = sqlite3.connect("noticias.db")
    preparar_tabela_llama(con)
    noticias = obter_30_noticias(con)
    print(f"Iniciando classificação de {len(noticias)} notícias com Llama 3.2 via Ollama Local...")
    
    ja_feitos = {r[0] for r in con.execute("SELECT noticia_id FROM classificacoes_llama WHERE tipo IS NOT NULL")}
    
    t_inicio = time.time()
    for idx, (id_, portal, titulo, texto, n_palavras) in enumerate(noticias, 1):
        if id_ in ja_feitos:
            print(f"[{idx:02d}/{len(noticias)}] ID {id_:<5} ({portal:<14}): Já classificado.")
            continue
        print(f"[{idx:02d}/{len(noticias)}] ID {id_:<5} ({portal:<14}): {titulo[:45]}...", end="", flush=True)
        t0 = time.time()
        res = classificar_uma_llama(titulo, texto, n_palavras)
        t_decorrido = time.time() - t0
        res["noticia_id"] = id_
        res["portal"] = portal
        res["titulo"] = titulo
        
        con.execute("""
            INSERT OR REPLACE INTO classificacoes_llama (
                noticia_id, tipo, tom_manchete, profundidade, postura,
                vies_politico, tema, justificativa, modelo, classificado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            id_,
            res.get("tipo"), res.get("tom_manchete"),
            res.get("profundidade"), res.get("postura"),
            res.get("vies_politico"), res.get("tema"),
            res.get("justificativa"), MODELO_LLAMA, time.strftime("%Y-%m-%d %H:%M:%S")
        ))
        con.commit()
        print(f" -> OK em {t_decorrido:.1f}s (tema: {res.get('tema')}, tom: {res.get('tom_manchete')})")
    
    t_total = time.time() - t_inicio
    print(f"\nClassificação com Llama 3.2 concluída em {t_total/60:.1f} minutos!")
    
    # Exportar para CSV (lendo tudo do SQLite)
    destino_csv = "classificacoes_llama.csv"
    linhas_db = con.execute("""
        SELECT c.noticia_id, n.portal, n.titulo,
               c.tipo, c.tom_manchete, c.profundidade,
               c.postura, c.vies_politico, c.tema, c.justificativa
        FROM classificacoes_llama c
        JOIN noticias n ON n.id = c.noticia_id
        ORDER BY c.noticia_id
    """).fetchall()
    with open(destino_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        cabecalho = [
            "noticia_id", "portal", "titulo",
            "tipo", "tom_manchete", "profundidade",
            "postura", "vies_politico", "tema", "justificativa"
        ]
        w.writerow(cabecalho)
        for r in linhas_db:
            w.writerow(list(r))
    print(f"Total de {len(linhas_db)} classificações exportadas para {destino_csv}.")

if __name__ == "__main__":
    main()
