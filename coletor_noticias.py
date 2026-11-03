#!/usr/bin/env python3
"""
Coletor de notícias brasileiras via RSS do Google News.

Portais: G1, CNN Brasil, Metrópoles, Poder360, Agência Brasil.
Para cada portal e cada dia, faz uma busca geral e uma busca por tema
(o RSS devolve no máximo ~100 itens por busca, então dividir por tema
recupera muito mais matérias). Salva tudo em SQLite, retoma de onde parou
e se mantém atualizado.

Instalação:
    pip install feedparser requests googlenewsdecoder

Uso:
    python coletor_noticias.py                      # histórico dos últimos 90 dias
    python coletor_noticias.py --days 7             # últimos 7 dias
    python coletor_noticias.py --start 2026-08-01 --end 2026-10-31

Manter atualizado:
    python coletor_noticias.py --atualizar          # preenche lacunas + recoleta ontem/hoje
    python coletor_noticias.py --agendar            # fica rodando e atualiza às 12:00 e 23:00
    python coletor_noticias.py --cron               # mostra comandos de cron / Windows

Links originais (necessário para baixar o texto completo):
    python coletor_noticias.py --decode --limite 20 # testa em 20 notícias
    python coletor_noticias.py --decode             # resolve os pendentes (só os agrupados,
                                                    # se agrupar_noticias.py já rodou)

Outros:
    python coletor_noticias.py --stats              # resumo do banco
    python coletor_noticias.py --limpar             # apaga notícias com data fora do período
    python coletor_noticias.py --export noticias.csv
    python coletor_noticias.py --log coletor.log    # grava log em arquivo
"""

import argparse
import csv
import logging
import os
import random
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
import requests

PORTAIS = {
    "G1": "g1.globo.com",
    "CNN Brasil": "cnnbrasil.com.br",
    "Metrópoles": "metropoles.com",
    "Poder360": "poder360.com.br",
    "Agência Brasil": "agenciabrasil.ebc.com.br",
}

# Buscas por tema. "geral" é a busca sem palavras-chave.
# Uma mesma notícia pode aparecer em vários temas; ela é salva uma vez só.
# Estes temas servem só para a COLETA. O tema de cada notícia na plataforma
# deve ser classificado depois, a partir do texto.
TEMAS = {
    "saude": "(saúde OR SUS OR vacina OR doença OR hospital OR médico OR epidemia OR remédio)",
    "esporte": "(futebol OR campeonato OR jogo OR Copa OR seleção OR Olimpíadas OR vôlei OR tênis)",
    "economia": "(economia OR inflação OR juros OR dólar OR PIB OR mercado OR imposto OR empresas)",
    "meio_ambiente": "(ambiente OR clima OR desmatamento OR Amazônia OR queimadas OR poluição OR enchente OR seca)",
    "educacao": "(educação OR escola OR universidade OR Enem OR professores OR alunos OR ensino OR MEC)",
    "politica": "(governo OR Congresso OR Senado OR Câmara OR STF OR ministro OR deputado OR eleições)",
    "cultura": "(cultura OR cinema OR música OR show OR festival OR livro OR teatro OR exposição)",
}

DIAS_HISTORICO = 90  # janela de histórico padrão (3 meses)
TOLERANCIA_DIAS = 1  # aceita notícia publicada até 1 dia antes/depois do dia buscado

RSS_URL = (
    "https://news.google.com/rss/search?q={query}"
    "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
)
LIMITE_RSS = 95  # o RSS devolve ~100 itens; acima disso pode haver truncamento
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("coletor")
logging.getLogger("httpx").setLevel(logging.WARNING)  # silencia o log do decodificador


# ---------------------------------------------------------------- banco
def colunas(con, tabela: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}


def abrir_banco(caminho: str) -> sqlite3.Connection:
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS noticias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal TEXT NOT NULL,
            titulo TEXT NOT NULL,
            link TEXT NOT NULL UNIQUE,
            link_original TEXT,
            publicado_em TEXT,
            resumo TEXT,
            coletado_em TEXT NOT NULL,
            dia_busca TEXT,
            decode_tentativas INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_noticias_data ON noticias(publicado_em);
        CREATE INDEX IF NOT EXISTS idx_noticias_portal ON noticias(portal);
        CREATE INDEX IF NOT EXISTS idx_noticias_portal_titulo ON noticias(portal, titulo);
        CREATE TABLE IF NOT EXISTS noticia_tema (
            noticia_id INTEGER NOT NULL,
            tema TEXT NOT NULL,
            PRIMARY KEY (noticia_id, tema)
        );
        """
    )

    # Migração de bancos criados pela versão anterior
    cols = colunas(con, "noticias")
    if "dia_busca" not in cols:
        con.execute("ALTER TABLE noticias ADD COLUMN dia_busca TEXT")
    if "decode_tentativas" not in cols:
        con.execute("ALTER TABLE noticias ADD COLUMN decode_tentativas INTEGER NOT NULL DEFAULT 0")

    cols_coletas = colunas(con, "coletas")
    if cols_coletas and "tema" not in cols_coletas:
        log.info("Atualizando a tabela de coletas para o formato com temas...")
        con.executescript(
            """
            ALTER TABLE coletas RENAME TO coletas_antiga;
            CREATE TABLE coletas (
                portal TEXT NOT NULL,
                dia TEXT NOT NULL,
                tema TEXT NOT NULL,
                qtd INTEGER NOT NULL,
                truncado INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (portal, dia, tema)
            );
            INSERT INTO coletas (portal, dia, tema, qtd, truncado)
                SELECT portal, dia, 'geral', qtd, truncado FROM coletas_antiga;
            DROP TABLE coletas_antiga;
            """
        )
    con.execute(
        """CREATE TABLE IF NOT EXISTS coletas (
               portal TEXT NOT NULL,
               dia TEXT NOT NULL,
               tema TEXT NOT NULL,
               qtd INTEGER NOT NULL,
               truncado INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (portal, dia, tema)
           )"""
    )
    con.commit()
    return con


def ja_coletado(con, portal: str, dia: date, tema: str) -> bool:
    cur = con.execute(
        "SELECT 1 FROM coletas WHERE portal=? AND dia=? AND tema=?",
        (portal, dia.isoformat(), tema),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------- rede
def baixar_feed(url: str, tentativas: int = 5):
    """Baixa o RSS com retry e backoff exponencial (trata 429/5xx)."""
    for n in range(1, tentativas + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return feedparser.parse(r.content)
            if r.status_code in (429, 500, 502, 503, 504):
                espera = min(300, (2 ** n) * 5) + random.uniform(0, 3)
                log.warning(
                    "HTTP %s (tentativa %d/%d). Aguardando %.0fs...",
                    r.status_code, n, tentativas, espera,
                )
                time.sleep(espera)
                continue
            log.error("HTTP %s inesperado para %s", r.status_code, url)
            return None
        except requests.RequestException as e:
            espera = (2 ** n) * 3
            log.warning("Erro de rede: %s. Aguardando %ds...", e, espera)
            time.sleep(espera)
    return None


def montar_url(dominio: str, dia: date, termos: str = "") -> str:
    proximo = dia + timedelta(days=1)
    partes = [f"site:{dominio}"]
    if termos:
        partes.append(termos)
    partes.append(f"after:{dia.isoformat()} before:{proximo.isoformat()}")
    return RSS_URL.format(query=quote(" ".join(partes), safe=":"))


# ---------------------------------------------------------------- parsing
def limpar_titulo(titulo: str) -> str:
    # O Google adiciona " - Nome do Portal" no fim do título
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", titulo).strip()


def limpar_html(texto: str) -> str:
    return re.sub(r"<[^>]+>", " ", texto or "").replace("&nbsp;", " ").strip()


def data_publicacao(entrada) -> datetime | None:
    bruto = entrada.get("published")
    if not bruto:
        return None
    try:
        return parsedate_to_datetime(bruto)
    except (TypeError, ValueError):
        return None


def dentro_do_periodo(publicado: datetime | None, dia: date) -> bool:
    """O filtro after:/before: do Google vaza matérias antigas. Descarta tudo
    que estiver a mais de TOLERANCIA_DIAS do dia buscado."""
    if publicado is None:
        return True
    return abs((publicado.date() - dia).days) <= TOLERANCIA_DIAS


# ---------------------------------------------------------------- coleta
def noticia_existente(con, portal: str, titulo: str, link: str, dia: date) -> int | None:
    """Reconhece uma notícia já salva: pelo link do Google ou, se o Google
    mudou o link, pelo mesmo portal + mesmo título em até 1 dia de diferença.
    Assim links e textos já baixados são aproveitados."""
    linha = con.execute("SELECT id, dia_busca FROM noticias WHERE link = ?", (link,)).fetchone()
    if linha is None:
        linha = con.execute(
            """SELECT id, dia_busca FROM noticias
               WHERE portal = ? AND titulo = ?
                 AND (dia_busca IS NULL OR dia_busca BETWEEN ? AND ?)
               LIMIT 1""",
            (portal, titulo, (dia - timedelta(days=1)).isoformat(),
             (dia + timedelta(days=1)).isoformat()),
        ).fetchone()
    if linha is None:
        return None
    id_, dia_busca = linha
    if dia_busca is None:
        con.execute("UPDATE noticias SET dia_busca = ? WHERE id = ?", (dia.isoformat(), id_))
    return id_


def coletar(con, portal: str, dominio: str, dia: date, tema: str) -> int:
    feed = baixar_feed(montar_url(dominio, dia, TEMAS[tema]))
    if feed is None:
        log.error("Falha em %s %s [%s]. Será tentado novamente na próxima execução.",
                  portal, dia, tema)
        return -1

    agora = datetime.now().isoformat(timespec="seconds")
    novas = descartadas = 0
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        publicado = data_publicacao(e)
        if not dentro_do_periodo(publicado, dia):
            descartadas += 1
            continue
        titulo = limpar_titulo(e.get("title", ""))
        id_ = noticia_existente(con, portal, titulo, link, dia)
        if id_ is None:
            cur = con.execute(
                """INSERT INTO noticias
                   (portal, titulo, link, publicado_em, resumo, coletado_em, dia_busca)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    portal,
                    titulo,
                    link,
                    publicado.isoformat() if publicado else None,
                    limpar_html(e.get("summary", "")),
                    agora,
                    dia.isoformat(),
                ),
            )
            id_ = cur.lastrowid
            novas += 1
        con.execute("INSERT OR IGNORE INTO noticia_tema (noticia_id, tema) VALUES (?, ?)",
                    (id_, tema))

    total = len(feed.entries)
    truncado = 1 if total >= LIMITE_RSS else 0
    if truncado:
        log.warning("%s %s [%s]: %d itens (possível truncamento)", portal, dia, tema, total)
    if descartadas:
        log.info("%s %s [%s]: %d notícias fora da data descartadas",
                 portal, dia, tema, descartadas)
    con.execute(
        "INSERT OR REPLACE INTO coletas (portal, dia, tema, qtd, truncado) VALUES (?,?,?,?,?)",
        (portal, dia.isoformat(), tema, total, truncado),
    )
    con.commit()
    return novas


def executar_periodo(con, inicio: date, fim: date, delay: float,
                     recoletar_ultimos: int = 0) -> int:
    """Coleta [inicio, fim] para todos os portais e temas. Pula buscas já
    feitas, exceto as dos últimos N dias (o Google indexa com atraso)."""
    dias = [inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)]
    limite_recoleta = fim - timedelta(days=recoletar_ultimos - 1) if recoletar_ultimos else None

    pendentes = [
        (dia, portal, dominio, tema)
        for dia in dias
        for portal, dominio in PORTAIS.items()
        for tema in TEMAS
        if (limite_recoleta is not None and dia >= limite_recoleta)
        or not ja_coletado(con, portal, dia, tema)
    ]
    minutos = len(pendentes) * (delay + 1.25) / 60
    log.info("Período: %s a %s | %d buscas pendentes (estimativa: %.0f min)",
             inicio, fim, len(pendentes), minutos)

    total_novas = 0
    for i, (dia, portal, dominio, tema) in enumerate(pendentes, 1):
        novas = coletar(con, portal, dominio, dia, tema)
        if novas >= 0:
            total_novas += novas
            log.info("[%d/%d] %s | %s | %s | +%d novas",
                     i, len(pendentes), dia, portal, tema, novas)
        time.sleep(delay + random.uniform(0, 1.5))

    log.info("Concluído. %d notícias novas salvas.", total_novas)
    return total_novas


def atualizar(con, args) -> int:
    """Preenche lacunas e recoleta ontem e hoje.
    Na primeira execução, faz o histórico completo."""
    fim = date.today()
    inicio = fim - timedelta(days=args.days)
    return executar_periodo(con, inicio, fim, args.delay, recoletar_ultimos=2)


def limpar(con):
    """Apaga notícias com data muito anterior ao primeiro dia buscado
    (lixo que o filtro de data do Google deixou passar em versões antigas)."""
    primeiro = con.execute("SELECT MIN(dia) FROM coletas").fetchone()[0]
    if not primeiro:
        print("Nada a limpar.")
        return
    corte = (date.fromisoformat(primeiro) - timedelta(days=TOLERANCIA_DIAS + 1)).isoformat()
    cur = con.execute(
        "DELETE FROM noticias WHERE publicado_em IS NOT NULL AND substr(publicado_em,1,10) < ?",
        (corte,),
    )
    con.commit()
    print(f"{cur.rowcount} notícias anteriores a {corte} apagadas.")


# ---------------------------------------------------------------- links
def carregar_decodificador():
    try:
        from googlenewsdecoder import gnewsdecoder
        return gnewsdecoder
    except ImportError:
        pass
    try:
        from googlenewsdecoder import new_decoderv1
        return new_decoderv1
    except ImportError:
        log.error("Instale com: pip install googlenewsdecoder")
        raise SystemExit(1)


def resolver_links(con, limite: int | None, todas: bool = False):
    """Resolve o link original das notícias que ainda não têm.
    Se o agrupamento (agrupar_noticias.py) já rodou, resolve só as notícias
    de grupos com 2 ou mais portais, a não ser que --todas seja usado.
    Desiste de uma notícia depois de 3 tentativas que falharam."""
    decodificar = carregar_decodificador()
    tem_grupos = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='grupos'").fetchone()
    if tem_grupos and not todas:
        log.info("Resolvendo só notícias de grupos com 2+ portais (use --todas para todas)")
        sql = """SELECT n.id, n.portal, n.link FROM noticias n
                 JOIN noticia_grupo ng ON ng.noticia_id = n.id
                 JOIN grupos g ON g.id = ng.grupo_id
                 WHERE g.n_portais >= 2
                   AND n.link_original IS NULL AND n.decode_tentativas < 3
                 ORDER BY g.n_portais DESC, n.publicado_em DESC"""
    else:
        sql = """SELECT id, portal, link FROM noticias
                 WHERE link_original IS NULL AND decode_tentativas < 3
                 ORDER BY publicado_em DESC"""
    linhas = con.execute(sql + (" LIMIT ?" if limite else ""),
                         (limite,) if limite else ()).fetchall()
    if not linhas:
        print("Nenhum link pendente.")
        return
    log.info("Resolvendo %d links...", len(linhas))

    ok = falhas = 0
    for i, (id_, portal, link) in enumerate(linhas, 1):
        url = None
        try:
            res = decodificar(link, interval=1)
            if res.get("status") or res.get("success"):
                url = res.get("decoded_url")
        except Exception as e:  # a biblioteca quebra de vez em quando
            log.debug("Falha ao decodificar %s: %s", link, e)
        if url:
            con.execute("UPDATE noticias SET link_original=? WHERE id=?", (url, id_))
            ok += 1
            if ok <= 3:
                log.info("Exemplo (%s): %s", portal, url)
        else:
            con.execute(
                "UPDATE noticias SET decode_tentativas = decode_tentativas + 1 WHERE id=?",
                (id_,),
            )
            falhas += 1
        if i % 20 == 0:
            con.commit()
            log.info("[%d/%d] ok: %d | falhas: %d", i, len(linhas), ok, falhas)
    con.commit()
    taxa = 100 * ok / len(linhas)
    log.info("Concluído. ok: %d | falhas: %d | sucesso: %.0f%%", ok, falhas, taxa)


# ---------------------------------------------------------------- export / stats
def exportar_csv(con, destino: str):
    cur = con.execute(
        """SELECT portal, titulo, publicado_em, dia_busca, link, link_original, resumo
           FROM noticias ORDER BY publicado_em"""
    )
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["portal", "titulo", "publicado_em", "dia_busca",
                    "link", "link_original", "resumo"])
        w.writerows(cur)
    log.info("Exportado para %s", destino)


def mostrar_stats(con):
    total = con.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    if not total:
        print("Banco vazio.")
        return
    ini, fim = con.execute(
        "SELECT MIN(publicado_em), MAX(publicado_em) FROM noticias").fetchone()
    print(f"\nTotal: {total} notícias | de {ini[:10] if ini else '?'} a {fim[:10] if fim else '?'}")

    print("\nPor portal:              notícias   com link original")
    for portal, qtd, com_link in con.execute(
            """SELECT portal, COUNT(*), COUNT(link_original)
               FROM noticias GROUP BY portal ORDER BY 2 DESC"""):
        print(f"  {portal:<22} {qtd:>8}   {com_link:>8}")

    dias = con.execute("SELECT COUNT(DISTINCT dia) FROM coletas").fetchone()[0]
    print(f"\nDias coletados: {dias}")
    print("\nNotícias por tema (uma notícia pode ter mais de um tema):")
    for tema, qtd in con.execute(
            "SELECT tema, COUNT(*) FROM noticia_tema GROUP BY tema ORDER BY 2 DESC"):
        print(f"  {tema:<16} {qtd:>8}")
    print("\nBuscas truncadas por tema (de quantas feitas):")
    for tema, feitas, trunc in con.execute(
            """SELECT tema, COUNT(*), SUM(truncado) FROM coletas
               GROUP BY tema ORDER BY 3 DESC"""):
        print(f"  {tema:<16} {trunc or 0:>4} de {feitas}")
    print()


# ---------------------------------------------------------------- agendador
def parse_horarios(texto: str) -> list[tuple[int, int]]:
    horarios = []
    for h in texto.split(","):
        try:
            hh, mm = h.strip().split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
            horarios.append((hh, mm))
        except ValueError:
            raise SystemExit(f"Horário inválido: '{h}'. Use o formato HH:MM, ex.: 12:00,23:00")
    return sorted(set(horarios))


def proxima_execucao(horarios: list[tuple[int, int]], agora: datetime) -> datetime:
    candidatos = []
    for hh, mm in horarios:
        c = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c <= agora:
            c += timedelta(days=1)
        candidatos.append(c)
    return min(candidatos)


def agendar(args):
    """Fica rodando e executa a atualização nos horários definidos."""
    horarios = parse_horarios(args.horarios)
    log.info("Agendador ativo. Horários: %s (Ctrl+C para parar)",
             ", ".join(f"{h:02d}:{m:02d}" for h, m in horarios))

    def rodar():
        con = abrir_banco(args.db)
        try:
            atualizar(con, args)
        except Exception:
            log.exception("Erro na atualização; tentará de novo no próximo horário")
        finally:
            con.close()

    if not args.sem_inicial:
        rodar()

    try:
        while True:
            prox = proxima_execucao(horarios, datetime.now())
            log.info("Próxima atualização: %s", prox.strftime("%d/%m/%Y %H:%M"))
            while True:
                restante = (prox - datetime.now()).total_seconds()
                if restante <= 0:
                    break
                time.sleep(min(60, restante))
            rodar()
    except KeyboardInterrupt:
        log.info("Agendador encerrado.")


def mostrar_agendamento(args):
    """Imprime os comandos para agendar com cron (Linux/Mac) ou Windows."""
    horarios = parse_horarios(args.horarios)
    script = os.path.abspath(__file__)
    pasta = os.path.dirname(script)
    py = sys.executable
    db = os.path.abspath(args.db)

    print("\n=== Linux / Mac (rode 'crontab -e' e cole estas linhas) ===")
    for hh, mm in horarios:
        print(f'{mm} {hh} * * * cd "{pasta}" && "{py}" "{script}" --atualizar '
              f'--db "{db}" --log "{os.path.join(pasta, "coletor.log")}"')

    print("\n=== Windows (rode no Prompt de Comando) ===")
    for hh, mm in horarios:
        print(f'schtasks /Create /SC DAILY /ST {hh:02d}:{mm:02d} '
              f'/TN "ColetorNoticias_{hh:02d}{mm:02d}" '
              f'/TR "\\"{py}\\" \\"{script}\\" --atualizar --db \\"{db}\\""')

    print("\nDica: se preferir não usar cron, rode "
          "'python coletor_noticias.py --agendar' e deixe a janela aberta.\n")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Coletor de notícias via Google News RSS")
    ap.add_argument("--days", type=int, default=DIAS_HISTORICO,
                    help=f"dias de histórico (padrão: {DIAS_HISTORICO})")
    ap.add_argument("--start", type=date.fromisoformat, help="data inicial (AAAA-MM-DD)")
    ap.add_argument("--end", type=date.fromisoformat, help="data final (AAAA-MM-DD)")
    ap.add_argument("--atualizar", "--daily", dest="atualizar", action="store_true",
                    help="preenche lacunas e recoleta ontem/hoje (use em rotinas diárias)")
    ap.add_argument("--agendar", action="store_true",
                    help="fica rodando e atualiza nos horários de --horarios")
    ap.add_argument("--horarios", default="12:00,23:00",
                    help="horários do agendador, separados por vírgula (padrão: 12:00,23:00)")
    ap.add_argument("--sem-inicial", action="store_true",
                    help="com --agendar, não atualiza ao iniciar; espera o primeiro horário")
    ap.add_argument("--cron", action="store_true",
                    help="mostra os comandos para agendar via cron/Windows e sai")
    ap.add_argument("--decode", action="store_true",
                    help="resolve o link original das notícias já coletadas e sai")
    ap.add_argument("--limite", type=int, help="com --decode, quantas notícias processar")
    ap.add_argument("--todas", action="store_true",
                    help="com --decode, resolve todas as notícias, não só as agrupadas")
    ap.add_argument("--limpar", action="store_true",
                    help="apaga notícias com data fora do período coletado e sai")
    ap.add_argument("--delay", type=float, default=4.0, help="segundos entre requisições")
    ap.add_argument("--db", default="noticias.db", help="arquivo SQLite")
    ap.add_argument("--log", metavar="ARQUIVO", help="grava o log também em arquivo")
    ap.add_argument("--stats", action="store_true", help="mostra resumo do banco e sai")
    ap.add_argument("--export", metavar="CSV", help="exporta o banco para CSV e sai")
    args = ap.parse_args()

    if args.log:
        fh = logging.FileHandler(args.log, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)

    if args.cron:
        mostrar_agendamento(args)
        return

    if args.agendar:
        agendar(args)
        return

    con = abrir_banco(args.db)

    if args.stats:
        mostrar_stats(con)
    elif args.export:
        exportar_csv(con, args.export)
    elif args.limpar:
        limpar(con)
    elif args.decode:
        resolver_links(con, args.limite, args.todas)
    elif args.atualizar:
        atualizar(con, args)
    else:
        fim = args.end or date.today()
        inicio = args.start or (fim - timedelta(days=args.days))
        executar_periodo(con, inicio, fim, args.delay)


if __name__ == "__main__":
    main()
