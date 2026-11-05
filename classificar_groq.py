# -*- coding: utf-8 -*-
"""
Classificação de Notícias com Groq (Qwen 2.5 27B)
Executa sobre a amostra das 30 notícias para benchmark comparativo com o Gemini.
Uso: python classificar_groq.py
"""

import os
import csv
import json
import time
import sqlite3
import requests
from collections import Counter
from classificar_noticias import DIMENSOES, MAX_CARACTERES, montar_prompt

ARQ_CHAVE = ".groq_key"
MODELO_GROQ = "qwen/qwen3.8-27b"
URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"

def carregar_chave():
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()
    if os.path.exists(ARQ_CHAVE):
        with open(ARQ_CHAVE, encoding="utf-8") as f:
            return f.read().strip()
    raise SystemExit("Chave da Groq não encontrada em .groq_key.")

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
    # manter a ordem exata do validacao.csv
    ordem_map = {id_: idx for idx, id_ in enumerate(ids)}
    linhas.sort(key=lambda x: ordem_map.get(x[0], 999))
    return linhas

def preparar_tabela_groq(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS classificacoes_groq (
            noticia_id INTEGER PRIMARY KEY,
            tipo TEXT,
            tipo_confianca REAL,
            tom_manchete TEXT,
            tom_manchete_confianca REAL,
            profundidade TEXT,
            profundidade_confianca REAL,
            postura TEXT,
            postura_confianca REAL,
            vies_politico TEXT,
            vies_politico_confianca REAL,
            tema TEXT,
            tema_confianca REAL,
            justificativa TEXT,
            modelo TEXT,
            classificado_em TEXT
        )
    """)
    con.commit()

def classificar_uma_groq(chave, titulo, texto, n_palavras):
    so_manchete = (n_palavras or 0) < 80 or not texto
    prompt = montar_prompt(titulo, texto, so_manchete)
    instrucao_json = (
        "\n\nResponda ESTRITAMENTE em formato JSON com as seguintes chaves:\n"
        "tipo, tipo_confianca, tom_manchete, tom_manchete_confianca, "
        "profundidade, profundidade_confianca, postura, postura_confianca, "
        "vies_politico, vies_politico_confianca, tema, tema_confianca, justificativa.\n"
        "Cada chave categórica DEVE usar exclusivamente um dos rótulos definidos na descrição."
    )
    prompt_completo = prompt + instrucao_json
    
    headers = {
        "Authorization": f"Bearer {chave}",
        "Content-Type": "application/json"
    }
    body = {
        "model": MODELO_GROQ,
        "messages": [{"role": "user", "content": prompt_completo}],
        "temperature": 0,
        "response_format": {"type": "json_object"}
    }
    
    for tentativa in range(1, 6):
        try:
            r = requests.post(URL_GROQ, headers=headers, json=body, timeout=45)
            if r.status_code == 200:
                resp_json = r.json()
                raw_text = resp_json["choices"][0]["message"]["content"]
                return json.loads(raw_text)
            elif r.status_code == 429:
                wait_time = 10 * tentativa
                print(f" [429 rate limit] aguardando {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                print(f" [HTTP {r.status_code}] {r.text[:100]} - tentativa {tentativa}")
                time.sleep(5)
        except Exception as e:
            print(f" [Erro de conexão: {e}] - tentativa {tentativa}")
            time.sleep(5)
    
    raise RuntimeError("Falha após 5 tentativas na API da Groq.")

def main():
    chave = carregar_chave()
    con = sqlite3.connect("noticias.db")
    preparar_tabela_groq(con)
    noticias = obter_30_noticias(con)
    print(f"Iniciando classificação de {len(noticias)} notícias com Groq ({MODELO_GROQ})...")
    
    resultados = []
    for idx, (id_, portal, titulo, texto, n_palavras) in enumerate(noticias, 1):
        print(f"[{idx}/{len(noticias)}] ID {id_} ({portal}): {titulo[:50]}...", end="", flush=True)
        res = classificar_uma_groq(chave, titulo, texto, n_palavras)
        res["noticia_id"] = id_
        res["portal"] = portal
        res["titulo"] = titulo
        resultados.append(res)
        
        # Gravar no SQLite
        con.execute("""
            INSERT OR REPLACE INTO classificacoes_groq (
                noticia_id, tipo, tipo_confianca, tom_manchete, tom_manchete_confianca,
                profundidade, profundidade_confianca, postura, postura_confianca,
                vies_politico, vies_politico_confianca, tema, tema_confianca,
                justificativa, modelo, classificado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            id_,
            res.get("tipo"), res.get("tipo_confianca"),
            res.get("tom_manchete"), res.get("tom_manchete_confianca"),
            res.get("profundidade"), res.get("profundidade_confianca"),
            res.get("postura"), res.get("postura_confianca"),
            res.get("vies_politico"), res.get("vies_politico_confianca"),
            res.get("tema"), res.get("tema_confianca"),
            res.get("justificativa"), MODELO_GROQ, time.strftime("%Y-%m-%d %H:%M:%S")
        ))
        con.commit()
        print(f" -> OK (tema: {res.get('tema')}, tom: {res.get('tom_manchete')})")
        
        # Pausa preventiva de 4 segundos para respeitar o limite de tokens/minuto da Groq
        time.sleep(4)
    
    # Exportar para CSV
    destino_csv = "classificacoes_groq.csv"
    with open(destino_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        cabecalho = [
            "noticia_id", "portal", "titulo",
            "tipo", "tipo_confianca",
            "tom_manchete", "tom_manchete_confianca",
            "profundidade", "profundidade_confianca",
            "postura", "postura_confianca",
            "vies_politico", "vies_politico_confianca",
            "tema", "tema_confianca", "justificativa"
        ]
        w.writerow(cabecalho)
        for r in resultados:
            w.writerow([
                r.get("noticia_id"), r.get("portal"), r.get("titulo"),
                r.get("tipo"), r.get("tipo_confianca"),
                r.get("tom_manchete"), r.get("tom_manchete_confianca"),
                r.get("profundidade"), r.get("profundidade_confianca"),
                r.get("postura"), r.get("postura_confianca"),
                r.get("vies_politico"), r.get("vies_politico_confianca"),
                r.get("tema"), r.get("tema_confianca"),
                r.get("justificativa")
            ])
            
    print(f"\nClassificação com Groq concluída com sucesso!")
    print(f"Resultados salvos em {destino_csv} e na tabela 'classificacoes_groq' do SQLite.")

if __name__ == "__main__":
    main()
