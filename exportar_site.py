#!/usr/bin/env python3
"""
Gera dados_site.json, o arquivo que a plataforma (Entrelinhas) lê.

Exporta os acontecimentos (grupos de notícias do mesmo fato) com pelo menos
N portais, e para cada notícia: portal, título, um trecho curto do início,
data e hora, link original, foto e, se já existirem, as classificações.

O texto completo NÃO é exportado (só um trecho de ~200 caracteres), para não
republicar o conteúdo dos portais.

Cada acontecimento recebe a capa que já estiver em site/capas/ (baixar_capas.py).

Uso:
    python exportar_site.py                    # acontecimentos com 3+ portais
    python exportar_site.py --min-portais 2    # inclui os de 2 portais (arquivo maior)
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BRASILIA = timezone(timedelta(hours=-3))

PORTAIS = {
    "G1": {"id": "g1", "nome": "G1", "tipo": "Nacional", "base": "Rio de Janeiro"},
    "CNN Brasil": {"id": "cnn-brasil", "nome": "CNN Brasil", "tipo": "Nacional", "base": "São Paulo"},
    "Metrópoles": {"id": "metropoles", "nome": "Metrópoles", "tipo": "Nacional", "base": "Brasília"},
    "Poder360": {"id": "poder360", "nome": "Poder360", "tipo": "Nacional", "base": "Brasília"},
    "Agência Brasil": {"id": "agencia-brasil", "nome": "Agência Brasil", "tipo": "Pública", "base": "Brasília"},
}
# ids de tema usados pelo site
TEMAS_SITE = {
    "politica": "politica", "economia": "economia", "saude": "saude", "educacao": "educacao",
    "meio_ambiente": "meio-ambiente", "esporte": "esporte",
    "cultura": "cultura",
    "cultura/mundo": "cultura",
    "entretenimento": "entretenimento",
}
ORDEM_TEMAS = list(TEMAS_SITE)  # desempate
# classificação: coluna no banco -> (id do critério no site, rótulos na ordem das colunas)
CRITERIOS = {
    "tipo": ("tipo", ["informativa", "analitica", "opinativa"]),
    "tom_manchete": ("tom", ["negativo", "neutro", "positivo"]),
    "profundidade": ("prof", ["superficial", "intermediaria", "aprofundada"]),
    "postura": ("postura", ["a_favor", "neutra", "contra"]),
    "vies_politico": ("vies", ["esquerda", "neutro", "direita"]),
}
TAM_TRECHO = 200
# Crédito no começo do texto da Agência Brasil:
# "Foto Agência Fulano/Agência Brasil Geral Rio de Janeiro (RJ), 02/09/2026 – ..."
RE_CREDITO_AGENCIA = re.compile(r"^Foto Ag[eê]ncia .{0,150}?\d{2}/\d{2}/\d{4}\s*[-–—]?\s*")
# Legenda de foto no começo do texto: "Fulano em evento — Foto: Reprodução" ou "— Foto: Nome/Agência"
RE_LEGENDA = re.compile(
    r"^.{0,250}?[—–-]\s*Foto:\s*(?:(?:Reprodu[cç][aã]o|Divulga[cç][aã]o|Arquivo)(?:/\S+)?"
    r"|[^/.]{1,60}?/(?:Ag[eê]ncia \w+|C[aâ]mara dos Deputados|Arquivo \w+|\S+))\s*")


def existe(con, tabela):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (tabela,)).fetchone() is not None


def trecho(texto: str | None) -> str:
    """Início do texto, cortado no fim de uma frase quando possível."""
    if not texto:
        return ""
    t = re.sub(r"\s+", " ", texto).strip()
    t = RE_LEGENDA.sub("", t, count=1).strip()
    t = RE_CREDITO_AGENCIA.sub("", t, count=1).strip()
    if len(t) <= TAM_TRECHO:
        return t
    corte = t[:TAM_TRECHO]
    fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
    if fim > 80:
        return corte[:fim + 1]
    return corte[:corte.rfind(" ")].rstrip(",;:") + "…"


def ler_capas(pasta):
    """fids com capa em alta qualidade (do baixar_capas.py)."""
    try:
        if os.path.isdir(pasta):
            return {f[:-5] for f in os.listdir(pasta) if f.endswith(".webp")}
        if os.path.exists("capas.json"):
            return set(json.load(open("capas.json", encoding="utf-8")))
        if os.path.exists("capas.zip"):
            with zipfile.ZipFile("capas.zip") as z:
                return set(json.loads(z.read("capas.json")))
    except Exception:
        pass
    return set()


def para_brasilia(iso: str | None) -> datetime | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(BRASILIA)


def calcular_divergencias(con, min_portais=3):
    sql = """
        SELECT 
            g.id AS grupo_id,
            g.titulo AS grupo_titulo,
            substr(n.publicado_em, 1, 10) as data_dia,
            strftime('%Y-%W', n.publicado_em) as semana,
            COALESCE(gt.tema, c.tema, 'geral') AS tema,
            g.n_portais,
            n.id AS noticia_id,
            n.portal,
            n.titulo,
            n.link,
            c.tipo,
            c.tom_manchete,
            c.profundidade,
            c.postura,
            c.vies_politico
        FROM grupos g
        LEFT JOIN grupo_tema gt ON gt.grupo_id = g.id
        JOIN noticia_grupo ng ON ng.grupo_id = g.id
        JOIN noticias n ON n.id = ng.noticia_id
        JOIN classificacoes c ON c.noticia_id = n.id
        WHERE g.n_portais >= ? AND c.erro IS NULL
        ORDER BY n.publicado_em ASC
    """
    linhas = con.execute(sql, (min_portais,)).fetchall()
    grupos = defaultdict(list)
    for l in linhas:
        grupos[l[0]].append({
            "grupo_id": l[0], "grupo_titulo": l[1], "data_dia": l[2], "semana": l[3],
            "tema": l[4], "n_portais": l[5], "noticia_id": l[6], "portal": l[7],
            "titulo": l[8], "link": l[9], "tipo": l[10], "tom_manchete": l[11],
            "profundidade": l[12], "postura": l[13], "vies_politico": l[14]
        })

    semanal = defaultdict(lambda: {"total": 0, "outliers_tom": 0, "por_portal": Counter()})
    outliers_tom = []
    outliers_postura = []

    for gid, nots in grupos.items():
        por_portal = {}
        for n in nots:
            if n["portal"] not in por_portal:
                por_portal[n["portal"]] = n
        nots_unicas = list(por_portal.values())
        if len(nots_unicas) < min_portais:
            continue

        dia = nots_unicas[0]["data_dia"] or "2026-11-01"
        sem = nots_unicas[0]["semana"] or "2026-44"
        semanal[sem]["total"] += 1

        # Tom da Manchete
        toms = [n["tom_manchete"] for n in nots_unicas if n.get("tom_manchete")]
        if len(toms) >= min_portais:
            cont_tom = Counter(toms)
            mais_comum, qtd_mc = cont_tom.most_common(1)[0]
            if qtd_mc >= 2 and len(cont_tom) > 1:
                for n in nots_unicas:
                    if n["tom_manchete"] and n["tom_manchete"] != mais_comum and cont_tom[n["tom_manchete"]] == 1:
                        semanal[sem]["outliers_tom"] += 1
                        semanal[sem]["por_portal"][n["portal"]] += 1
                        outliers_tom.append({
                            "grupo_id": gid,
                            "grupo_titulo": n["grupo_titulo"],
                            "data": dia,
                            "semana": sem,
                            "tema": n["tema"],
                            "portal_outlier": n["portal"],
                            "tom_outlier": n["tom_manchete"],
                            "titulo_outlier": n["titulo"],
                            "tom_consenso": mais_comum,
                            "qtd_consenso": qtd_mc,
                            "total_portais": len(toms),
                            "outros_titulos": [
                                {"portal": o["portal"], "titulo": o["titulo"], "tom": o["tom_manchete"]}
                                for o in nots_unicas if o["portal"] != n["portal"]
                            ]
                        })

        # Postura
        posturas = [n["postura"] for n in nots_unicas if n.get("postura")]
        if len(posturas) >= min_portais:
            cont_pos = Counter(posturas)
            mais_comum, qtd_mc = cont_pos.most_common(1)[0]
            if qtd_mc >= 2 and len(cont_pos) > 1:
                for n in nots_unicas:
                    if n["postura"] and n["postura"] != mais_comum and cont_pos[n["postura"]] == 1:
                        outliers_postura.append({
                            "grupo_id": gid,
                            "grupo_titulo": n["grupo_titulo"],
                            "data": dia,
                            "tema": n["tema"],
                            "portal_outlier": n["portal"],
                            "postura_outlier": n["postura"],
                            "titulo_outlier": n["titulo"],
                            "postura_consenso": mais_comum,
                            "qtd_consenso": qtd_mc,
                            "total_portais": len(posturas),
                            "outros_titulos": [
                                {"portal": o["portal"], "titulo": o["titulo"], "postura": o["postura"]}
                                for o in nots_unicas if o["portal"] != n["portal"]
                            ]
                        })

    ranking_tom = Counter(o["portal_outlier"] for o in outliers_tom)
    ranking_postura = Counter(o["portal_outlier"] for o in outliers_postura)
    portais_ordem = ["Metrópoles", "G1", "CNN Brasil", "Agência Brasil", "Poder360"]
    ranking = []
    for p in portais_ordem:
        qtd_t = ranking_tom.get(p, 0)
        qtd_p = ranking_postura.get(p, 0)
        pct_t = round((qtd_t / len(outliers_tom) * 100), 1) if outliers_tom else 0
        ranking.append({
            "portal": p,
            "outliers_tom": qtd_t,
            "pct_tom": pct_t,
            "outliers_postura": qtd_p
        })

    DATAS_SEMANAS = {
        "2026-31": "03 a 09 de ago",
        "2026-32": "10 a 16 de ago",
        "2026-33": "17 a 23 de ago",
        "2026-34": "24 a 30 de ago",
        "2026-35": "31 de ago a 06 de set",
        "2026-36": "07 a 13 de set",
        "2026-37": "14 a 20 de set",
        "2026-38": "21 a 27 de set",
        "2026-39": "28 de set a 04 de out",
        "2026-40": "05 a 11 de out",
        "2026-41": "12 a 18 de out",
        "2026-42": "19 a 25 de out",
        "2026-43": "26 de out a 01 de nov",
        "2026-44": "02 a 08 de nov",
        "2026-45": "09 e 10 de nov"
    }

    serie_temporal = []
    for sem in sorted(semanal.keys()):
        d = semanal[sem]
        tot = d["total"]
        outl = d["outliers_tom"]
        taxa = round((outl / tot * 100), 1) if tot else 0.0
        rotulo_legivel = DATAS_SEMANAS.get(sem, sem)
        serie_temporal.append({
            "semana": sem,
            "rotulo": rotulo_legivel,
            "total_eventos": tot,
            "outliers": outl,
            "taxa": taxa,
            "por_portal": dict(d["por_portal"])
        })

    outliers_tom.sort(key=lambda x: x["data"], reverse=True)
    outliers_postura.sort(key=lambda x: x["data"], reverse=True)

    return {
        "total_eventos_analisados": len(grupos),
        "total_outliers_tom": len(outliers_tom),
        "taxa_geral_tom": round(len(outliers_tom) / len(grupos) * 100, 1) if grupos else 0,
        "total_outliers_postura": len(outliers_postura),
        "ranking_portais": ranking,
        "serie_temporal": serie_temporal,
        "casos_destaque": outliers_tom[:35]
    }


def main():
    ap = argparse.ArgumentParser(description="Gera dados_site.json para a plataforma")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--min-portais", type=int, default=3)
    ap.add_argument("--saida", default="site/dados_site.json")
    ap.add_argument("--capas", default="site/capas", help="pasta com as capas")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    if not existe(con, "grupos"):
        raise SystemExit("Rode antes: python agrupar_noticias.py")
    tem_textos = existe(con, "textos")
    tem_cls = existe(con, "classificacoes")
    tem_grupo_tema = existe(con, "grupo_tema")

    grupos = con.execute(
        "SELECT id, n_portais FROM grupos WHERE n_portais >= ? ORDER BY inicio",
        (args.min_portais,)).fetchall()

    capas = ler_capas(args.capas)
    subtemas, noticias = [], {}
    sem_tema = 0
    for gid, n_portais in grupos:
        linhas = con.execute(
            f"""SELECT n.id, n.portal, n.titulo, n.publicado_em, n.link_original, ng.similaridade,
                       {"t.texto, t.url_final, t.imagem_url, t.provavel_opiniao" if tem_textos
                        else "NULL, NULL, NULL, 0"}
                FROM noticia_grupo ng JOIN noticias n ON n.id = ng.noticia_id
                {"LEFT JOIN textos t ON t.noticia_id = n.id" if tem_textos else ""}
                WHERE ng.grupo_id = ? ORDER BY n.publicado_em""", (gid,)).fetchall()
        linhas = [l for l in linhas if l[1] in PORTAIS]
        if len({l[1] for l in linhas}) < args.min_portais:
            continue

        # tema do acontecimento: classificação (se houver) ou a maioria dos temas da busca
        tema = None
        if tem_grupo_tema:
            r = con.execute("SELECT tema FROM grupo_tema WHERE grupo_id = ?", (gid,)).fetchone()
            tema = r[0] if r else None
        if not tema:
            votos = Counter()
            for l in linhas:
                for (t,) in con.execute("SELECT tema FROM noticia_tema WHERE noticia_id = ?",
                                        (l[0],)):
                    votos[t] += 1
            if votos:
                tema = max(votos, key=lambda t: (votos[t], -ORDEM_TEMAS.index(t)))
        if tema not in TEMAS_SITE:
            sem_tema += 1
            continue

        # título do acontecimento: a notícia mais próxima do centro do grupo
        titulo = max(linhas, key=lambda l: l[5] or 0)[2]
        datas = [para_brasilia(l[3]) for l in linhas if l[3]]
        sid = f"a{gid}"

        itens = []
        for (nid, portal, tit, pub, link, _sim, texto, url_final, imagem, opiniao) in linhas:
            d = para_brasilia(pub)
            cls = {}
            if tem_cls:
                cols = ", ".join(f"{c}, {c}_confianca" for c in CRITERIOS)
                r = con.execute(f"SELECT {cols} FROM classificacoes "
                                f"WHERE noticia_id = ? AND erro IS NULL", (nid,)).fetchone()
                if r:
                    for k, (col, (crit, rotulos)) in enumerate(CRITERIOS.items()):
                        valor, conf = r[2 * k], r[2 * k + 1]
                        if valor in rotulos:
                            cls[crit] = [rotulos.index(valor), round(conf or 0, 3)]
            url = url_final or link or "#"
            itens.append({
                "fid": hashlib.sha1(url.encode()).hexdigest()[:12],
                "portal": PORTAIS[portal]["id"],
                "titulo": tit,
                "trecho": trecho(texto),
                "hora": d.strftime("%Hh%M") if d else "",
                "data": d.isoformat() if d else None,
                "url": url,
                "imagem": imagem,
                "opiniao": bool(opiniao),
                "cls": cls,
            })

        subtemas.append({
            "id": sid,
            "tema": TEMAS_SITE[tema],
            "titulo": titulo,
            "portais": len({l[1] for l in linhas}),
            "noticias": len(itens),
            "data": max(datas).isoformat() if datas else None,
            "fatos": [],
        })
        capa = next((n["fid"] for n in itens if n["fid"] in capas), None)
        if capa:
            subtemas[-1]["capa"] = capa
        noticias[sid] = itens

    if existe(con, "pacote_info"):
        # banco reduzido (preparar_pacote.py): período e total vêm do banco completo
        info = dict(con.execute("SELECT chave, valor FROM pacote_info"))
        periodo = (info["periodo_inicio"], info["periodo_fim"])
        total_noticias = int(info["total_coletadas"])
    else:
        periodo = con.execute("SELECT MIN(dia_busca), MAX(dia_busca) FROM noticias").fetchone()
        total_noticias = con.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    dados = {
        "gerado_em": datetime.now(BRASILIA).isoformat(timespec="seconds"),
        "periodo": {"inicio": periodo[0], "fim": periodo[1]},
        "total_coletadas": total_noticias,
        "min_portais": args.min_portais,
        "classificado": any(n["cls"] for lista in noticias.values() for n in lista),
        "portais": list(PORTAIS.values()),
        "subtemas": subtemas,
        "noticias": noticias,
        "divergencias": calcular_divergencias(con, args.min_portais),
    }
    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))

    n_not = sum(len(v) for v in noticias.values())
    print(f"\n{len(subtemas)} acontecimentos com {args.min_portais}+ portais, {n_not} notícias")
    if sem_tema:
        print(f"({sem_tema} acontecimentos sem tema identificado ficaram de fora)")
    print("Por tema:")
    for t, q in Counter(s["tema"] for s in subtemas).most_common():
        print(f"  {t:<16} {q:>5}")
    print(f"Com capa: {sum(1 for s in subtemas if s.get('capa'))} de {len(subtemas)}")
    print(f"Classificações incluídas: {'sim' if dados['classificado'] else 'ainda não'}")
    print(f"\nArquivo: {args.saida} ({os.path.getsize(args.saida) / 1e6:.1f} MB)\n")


if __name__ == "__main__":
    main()
