#!/usr/bin/env python3
"""
Baixa o texto completo das notícias e grava no mesmo noticias.db.

Pega as notícias que já têm link original (resolvido com
`python coletor_noticias.py --decode`). Se o agrupamento já rodou, baixa só
as notícias de grupos com 2 ou mais portais.

Para cada notícia:
- respeita o robots.txt do portal;
- baixa a página e extrai o corpo com trafilatura;
- guarda texto, autor, seção, data original, status e erro;
- marca `provavel_opiniao` (colunas, artigos, blogs, editoriais), para que
  não contaminem a análise de postura do portal.

Roda um portal por vez em paralelo (6 ao mesmo tempo), com intervalo entre
requisições ao mesmo portal. Retoma de onde parou.

Instalação:
    pip install trafilatura requests

Uso:
    python baixar_textos.py --limite 30          # teste: 30 notícias
    python baixar_textos.py                      # todas as pendentes
    python baixar_textos.py --portal G1          # só um portal
    python baixar_textos.py --stats              # taxa de sucesso por portal
    python baixar_textos.py --exemplo G1         # mostra um texto baixado, para conferir
    python baixar_textos.py --curtos Band        # lista as URLs que vieram com texto curto
    python baixar_textos.py --refazer-curtos     # baixa de novo as que vieram curtas
    python baixar_textos.py --refazer-imagens    # pega a imagem das que já foram baixadas
    python baixar_textos.py --diagnostico URL    # mostra de onde dá para tirar o texto
"""

import argparse
import json
import logging
import queue
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime
from urllib import robotparser
from urllib.parse import urlparse

import requests

try:
    import trafilatura
except ImportError:
    raise SystemExit("Instale com: pip install trafilatura")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DELAY_POR_PORTAL = 3.0   # segundos entre duas requisições ao mesmo portal
MAX_TENTATIVAS = 3
MIN_PALAVRAS = 80        # menos que isso: provável paywall, vídeo ou página sem texto

# Trechos de URL e de seção que indicam opinião
PADROES_OPINIAO_URL = re.compile(
    r"/(opiniao|opinioes|colunas?|colunistas?|blogs?|artigos?|analise|analises|"
    r"editorial|editoriais|cronicas?|ponto-de-vista)(/|-|$)",
    re.IGNORECASE,
)
PADROES_OPINIAO_SECAO = re.compile(
    r"opini|coluna|colunista|blog|artigo|an[aá]lise|editorial|cr[oô]nica",
    re.IGNORECASE,
)
# "Nome do Colunista | Título" é um formato comum de coluna
PADRAO_TITULO_COLUNA = re.compile(r"^[^|]{3,40}\s\|\s")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("textos")
for barulhento in ("trafilatura", "htmldate", "courlan", "urllib3", "charset_normalizer"):
    logging.getLogger(barulhento).setLevel(logging.CRITICAL)


# ---------------------------------------------------------------- banco
def preparar_tabela(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS textos (
            noticia_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,          -- ok, curto, bloqueado, robots, removida, falha
            erro TEXT,
            url_final TEXT,
            texto TEXT,
            n_palavras INTEGER,
            autor TEXT,
            secao TEXT,
            data_publicacao_original TEXT,
            provavel_opiniao INTEGER NOT NULL DEFAULT 0,
            motivo_opiniao TEXT,
            tentativas INTEGER NOT NULL DEFAULT 0,
            baixado_em TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_textos_status ON textos(status);
        """
    )
    colunas = {r[1] for r in con.execute("PRAGMA table_info(textos)")}
    if "imagem_url" not in colunas:
        con.execute("ALTER TABLE textos ADD COLUMN imagem_url TEXT")
        con.commit()


def pendentes(con, portal: str | None, limite: int | None, refazer_curtos=False,
              refazer_imagens=False):
    tem_grupos = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='grupos'").fetchone()
    filtro_grupo = ""
    if tem_grupos:
        filtro_grupo = """AND n.id IN (SELECT ng.noticia_id FROM noticia_grupo ng
                                       JOIN grupos g ON g.id = ng.grupo_id
                                       WHERE g.n_portais >= 2)"""
    sql = f"""
        SELECT n.id, n.portal, n.titulo, n.link_original
        FROM noticias n
        LEFT JOIN textos t ON t.noticia_id = n.id
        WHERE n.link_original IS NOT NULL
          AND (t.noticia_id IS NULL
               OR (t.status IN ('falha', 'bloqueado') AND t.tentativas < {MAX_TENTATIVAS})
               {"OR t.status = 'curto'" if refazer_curtos else ""}
               {"OR (t.status = 'ok' AND t.imagem_url IS NULL)" if refazer_imagens else ""})
          {filtro_grupo}
          {"AND n.portal = ?" if portal else ""}
        ORDER BY n.publicado_em DESC
    """
    params = [portal] if portal else []
    if limite:
        sql += " LIMIT ?"
        params.append(limite)
    return con.execute(sql, params).fetchall()


# ---------------------------------------------------------------- robots.txt
class Robots:
    """Lê e guarda o robots.txt de cada site."""

    def __init__(self):
        self.cache = {}
        self.trava = threading.Lock()

    def permite(self, url: str) -> bool:
        partes = urlparse(url)
        base = f"{partes.scheme}://{partes.netloc}"
        with self.trava:
            if base not in self.cache:
                rp = robotparser.RobotFileParser()
                try:
                    r = requests.get(base + "/robots.txt", timeout=15,
                                     headers={"User-Agent": USER_AGENT})
                    if r.status_code == 200:
                        rp.parse(r.text.splitlines())
                    else:
                        rp.parse([])  # sem robots.txt: tudo permitido
                except requests.RequestException:
                    rp.parse([])
                self.cache[base] = rp
            return self.cache[base].can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------- extração
def detectar_opiniao(url: str, secao: str | None, titulo: str) -> str | None:
    """Devolve o motivo se a notícia parece ser opinião, ou None."""
    m = PADROES_OPINIAO_URL.search(urlparse(url).path)
    if m:
        return f"url: /{m.group(1)}"
    if secao and PADROES_OPINIAO_SECAO.search(secao):
        return f"seção: {secao[:40]}"
    if PADRAO_TITULO_COLUNA.match(titulo or ""):
        return "título no formato 'Nome | Título'"
    return None


RE_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def corpo_ld_json(html: str) -> str:
    """Muitos portais colocam o texto inteiro no campo articleBody dos dados
    estruturados (JSON-LD), mesmo quando o HTML visível vem incompleto."""
    melhor = ""

    def procurar(obj):
        nonlocal melhor
        if isinstance(obj, dict):
            corpo = obj.get("articleBody")
            if isinstance(corpo, str) and len(corpo) > len(melhor):
                melhor = corpo
            for v in obj.values():
                procurar(v)
        elif isinstance(obj, list):
            for v in obj:
                procurar(v)

    for bloco in RE_LD_JSON.findall(html):
        try:
            procurar(json.loads(bloco.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    melhor = re.sub(r"<[^>]+>", " ", melhor)
    return re.sub(r"\s+", " ", melhor).strip()


RE_OG_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>',
    re.IGNORECASE,
)


def imagem_principal(html: str, dados: dict | None) -> str | None:
    """Endereço da foto principal (a mesma usada ao compartilhar o link)."""
    if dados and dados.get("image"):
        return dados["image"]
    tag = RE_OG_IMAGE.search(html)
    if tag:
        m = re.search(r'content=["\']([^"\']+)', tag.group(0))
        if m:
            return m.group(1)
    return None


def extrair(html: str, url: str, **modo) -> dict | None:
    bruto = trafilatura.extract(
        html, url=url, output_format="json", with_metadata=True,
        include_comments=False, include_tables=False, **modo,
    )
    return json.loads(bruto) if bruto else None


def baixar_uma(url: str, titulo: str, robots: Robots) -> dict:
    agora = datetime.now().isoformat(timespec="seconds")
    if not robots.permite(url):
        return {"status": "robots", "erro": "bloqueado pelo robots.txt", "baixado_em": agora}

    try:
        r = requests.get(url, timeout=30, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "pt-BR,pt;q=0.9",
        })
    except requests.RequestException as e:
        return {"status": "falha", "erro": f"rede: {type(e).__name__}", "baixado_em": agora}

    if r.status_code in (401, 402, 403, 429, 451):
        return {"status": "bloqueado", "erro": f"HTTP {r.status_code}",
                "url_final": r.url, "baixado_em": agora}
    if r.status_code in (404, 410):
        return {"status": "removida", "erro": f"HTTP {r.status_code}",
                "url_final": r.url, "baixado_em": agora}
    if r.status_code != 200:
        return {"status": "falha", "erro": f"HTTP {r.status_code}",
                "url_final": r.url, "baixado_em": agora}

    dados = extrair(r.text, r.url, favor_precision=True)
    if dados is None or len((dados.get("text") or "").split()) < MIN_PALAVRAS:
        # Modo rigoroso pode cortar o corpo em alguns layouts; tenta o modo amplo
        amplo = extrair(r.text, r.url, favor_recall=True)
        if amplo and len((amplo.get("text") or "").split()) > \
                len(((dados or {}).get("text") or "").split()):
            dados = amplo
    texto = ((dados or {}).get("text") or "").strip()
    if len(texto.split()) < MIN_PALAVRAS:
        alternativo = corpo_ld_json(r.text)
        if len(alternativo.split()) > len(texto.split()):
            texto = alternativo
            dados = dados or {}
    if dados is None:
        return {"status": "falha", "erro": "trafilatura não achou o corpo",
                "url_final": r.url, "baixado_em": agora}

    secao = dados.get("categories") or None
    n_palavras = len(texto.split())
    motivo = detectar_opiniao(r.url, secao, titulo)
    return {
        "status": "ok" if n_palavras >= MIN_PALAVRAS else "curto",
        "erro": None if n_palavras >= MIN_PALAVRAS else f"só {n_palavras} palavras",
        "url_final": r.url,
        "texto": texto,
        "n_palavras": n_palavras,
        "autor": dados.get("author"),
        "secao": secao,
        "data_publicacao_original": dados.get("date"),
        "provavel_opiniao": 1 if motivo else 0,
        "motivo_opiniao": motivo,
        "imagem_url": imagem_principal(r.text, dados),
        "baixado_em": agora,
    }


# ---------------------------------------------------------------- execução
def trabalhador(itens, robots, delay, saida: queue.Queue):
    """Processa as notícias de UM portal, respeitando o intervalo."""
    for id_, portal, titulo, url in itens:
        inicio = time.monotonic()
        try:
            res = baixar_uma(url, titulo, robots)
        except Exception as e:  # nunca deixa um erro derrubar a execução
            res = {"status": "falha", "erro": f"inesperado: {e}"[:200],
                   "baixado_em": datetime.now().isoformat(timespec="seconds")}
        saida.put((id_, portal, res))
        espera = delay - (time.monotonic() - inicio)
        if espera > 0:
            time.sleep(espera)


def gravar(con, id_, res):
    campos = ["status", "erro", "url_final", "texto", "n_palavras", "autor", "secao",
              "data_publicacao_original", "provavel_opiniao", "motivo_opiniao",
              "imagem_url", "baixado_em"]
    valores = [res.get(c) for c in campos]
    valores[campos.index("provavel_opiniao")] = res.get("provavel_opiniao", 0)
    con.execute(
        f"""INSERT INTO textos (noticia_id, {", ".join(campos)}, tentativas)
            VALUES (?, {", ".join("?" * len(campos))}, 1)
            ON CONFLICT(noticia_id) DO UPDATE SET
            {", ".join(f"{c}=excluded.{c}" for c in campos)},
            tentativas = textos.tentativas + 1""",
        [id_] + valores,
    )


def executar(con, portal, limite, delay, refazer_curtos=False, refazer_imagens=False):
    linhas = pendentes(con, portal, limite, refazer_curtos, refazer_imagens)
    if not linhas:
        print("Nenhuma notícia pendente com link original.")
        print("Rode antes: python coletor_noticias.py --decode")
        return

    por_portal = defaultdict(list)
    for linha in linhas:
        por_portal[linha[1]].append(linha)
    maior = max(len(v) for v in por_portal.values())
    log.info("%d notícias pendentes em %d portais (estimativa: %.0f min)",
             len(linhas), len(por_portal), maior * delay / 60)

    robots = Robots()
    saida = queue.Queue()
    threads = [threading.Thread(target=trabalhador, args=(itens, robots, delay, saida),
                                daemon=True) for itens in por_portal.values()]
    for t in threads:
        t.start()

    contagem = defaultdict(int)
    feitos = 0
    try:
        while feitos < len(linhas):
            try:
                id_, port, res = saida.get(timeout=5)
            except queue.Empty:
                if not any(t.is_alive() for t in threads):
                    break
                continue
            gravar(con, id_, res)
            feitos += 1
            contagem[res["status"]] += 1
            if feitos % 25 == 0 or feitos == len(linhas):
                con.commit()
                resumo = " | ".join(f"{k}: {v}" for k, v in sorted(contagem.items()))
                log.info("[%d/%d] %s", feitos, len(linhas), resumo)
    except KeyboardInterrupt:
        log.info("Interrompido. O que já foi baixado está salvo.")
    finally:
        con.commit()


# ---------------------------------------------------------------- relatórios
def mostrar_stats(con):
    linhas = con.execute(
        """SELECT n.portal, t.status, COUNT(*) FROM textos t
           JOIN noticias n ON n.id = t.noticia_id
           GROUP BY n.portal, t.status""").fetchall()
    if not linhas:
        print("Nenhum texto baixado ainda.")
        return
    tabela = defaultdict(dict)
    for portal, status, qtd in linhas:
        tabela[portal][status] = qtd
    colunas = ["ok", "curto", "bloqueado", "robots", "removida", "falha"]
    print(f"\n{'portal':<16}" + "".join(f"{c:>11}" for c in colunas) + "   sucesso")
    for portal, st in sorted(tabela.items()):
        total = sum(st.values())
        taxa = 100 * st.get("ok", 0) / total
        print(f"{portal:<16}" + "".join(f"{st.get(c, 0):>11}" for c in colunas)
              + f"   {taxa:>5.0f}%")

    print("\nProvável opinião (entre os textos ok):")
    for portal, op, total in con.execute(
            """SELECT n.portal, SUM(t.provavel_opiniao), COUNT(*) FROM textos t
               JOIN noticias n ON n.id = t.noticia_id WHERE t.status = 'ok'
               GROUP BY n.portal ORDER BY n.portal"""):
        print(f"  {portal:<16} {op or 0:>6} de {total}")

    com_img, total_ok = con.execute(
        "SELECT COUNT(imagem_url), COUNT(*) FROM textos WHERE status = 'ok'").fetchone()
    print(f"\nCom imagem: {com_img} de {total_ok} textos ok")

    media = con.execute(
        "SELECT AVG(n_palavras) FROM textos WHERE status = 'ok'").fetchone()[0]
    if media:
        print(f"\nTamanho médio dos textos: {media:.0f} palavras")

    print("\nErros mais comuns:")
    for erro, qtd in con.execute(
            """SELECT erro, COUNT(*) FROM textos WHERE status <> 'ok'
               GROUP BY erro ORDER BY 2 DESC LIMIT 5"""):
        print(f"  {qtd:>6}  {erro}")
    print()


def diagnosticar(url: str):
    """Baixa uma página e mostra de onde dá para tirar o texto."""
    r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT,
                                                "Accept-Language": "pt-BR,pt;q=0.9"})
    html = r.text
    with open("diagnostico.html", "w", encoding="utf-8") as f:
        f.write(html)
    paragrafos = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
    palavras_p = sum(len(re.sub(r"<[^>]+>", " ", p).split()) for p in paragrafos)
    rigoroso = extrair(html, r.url, favor_precision=True) or {}
    amplo = extrair(html, r.url, favor_recall=True) or {}
    ld = corpo_ld_json(html)
    print(f"\nHTTP {r.status_code} | {len(html):,} caracteres de HTML")
    print(f"Parágrafos <p> no HTML: {len(paragrafos)} ({palavras_p} palavras)")
    print(f"trafilatura rigoroso:   {len((rigoroso.get('text') or '').split())} palavras")
    print(f"trafilatura amplo:      {len((amplo.get('text') or '').split())} palavras")
    print(f"articleBody (JSON-LD):  {len(ld.split())} palavras")
    for marca in ("__NEXT_DATA__", "__NUXT__", "window.__INITIAL_STATE__", "<iframe", "<video",
                  "paywall", "assinante"):
        if marca.lower() in html.lower():
            print(f"Contém: {marca}")
    print("\nHTML salvo em diagnostico.html\n")


def mostrar_exemplo(con, portal):
    linha = con.execute(
        """SELECT n.titulo, t.url_final, t.autor, t.secao, t.data_publicacao_original,
                  t.provavel_opiniao, t.motivo_opiniao, t.n_palavras, t.texto, t.imagem_url
           FROM textos t JOIN noticias n ON n.id = t.noticia_id
           WHERE t.status = 'ok' AND n.portal = ? ORDER BY RANDOM() LIMIT 1""",
        (portal,)).fetchone()
    if not linha:
        print(f"Nenhum texto ok de {portal} ainda.")
        return
    titulo, url, autor, secao, data, op, motivo, n, texto, imagem = linha
    print(f"\nTítulo:   {titulo}\nURL:      {url}\nAutor:    {autor}\nSeção:    {secao}")
    print(f"Data:     {data}\nOpinião:  {'sim (' + motivo + ')' if op else 'não'}")
    print(f"Imagem:   {imagem}")
    print(f"Palavras: {n}\n\n{texto[:1200]}{'...' if len(texto) > 1200 else ''}\n")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Baixa o texto completo das notícias")
    ap.add_argument("--db", default="noticias.db")
    ap.add_argument("--limite", type=int, help="quantas notícias processar")
    ap.add_argument("--portal", help="processa só este portal (ex.: G1)")
    ap.add_argument("--delay", type=float, default=DELAY_POR_PORTAL,
                    help=f"segundos entre requisições ao mesmo portal (padrão {DELAY_POR_PORTAL})")
    ap.add_argument("--log", metavar="ARQUIVO", help="grava o log também em arquivo")
    ap.add_argument("--refazer-imagens", action="store_true",
                    help="baixa de novo as notícias ok que ainda não têm imagem")
    ap.add_argument("--refazer-curtos", action="store_true",
                    help="baixa de novo as notícias que vieram com texto curto")
    ap.add_argument("--curtos", metavar="PORTAL", help="lista as URLs com texto curto e sai")
    ap.add_argument("--diagnostico", metavar="URL", help="analisa uma página e sai")
    ap.add_argument("--stats", action="store_true", help="mostra a taxa de sucesso e sai")
    ap.add_argument("--exemplo", metavar="PORTAL", help="mostra um texto baixado e sai")
    args = ap.parse_args()

    if args.log:
        fh = logging.FileHandler(args.log, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)

    con = sqlite3.connect(args.db, check_same_thread=False)
    preparar_tabela(con)

    if args.diagnostico:
        diagnosticar(args.diagnostico)
    elif args.stats:
        mostrar_stats(con)
    elif args.curtos:
        for url, n in con.execute(
                """SELECT t.url_final, t.n_palavras FROM textos t
                   JOIN noticias n ON n.id = t.noticia_id
                   WHERE t.status = 'curto' AND n.portal = ?""", (args.curtos,)):
            print(f"{n:>4} palavras  {url}")
    elif args.exemplo:
        mostrar_exemplo(con, args.exemplo)
    else:
        executar(con, args.portal, args.limite, args.delay, args.refazer_curtos,
                 args.refazer_imagens)


if __name__ == "__main__":
    main()
