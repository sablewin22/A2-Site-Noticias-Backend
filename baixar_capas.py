#!/usr/bin/env python3
"""
Baixa a capa de cada acontecimento em boa qualidade.

Para cada acontecimento do dados_site.json que ainda não tem capa na pasta:
- baixa a foto ORIGINAL de cada notícia do grupo;
- escolhe a de maior resolução real (mais pixels);
- salva com até 640 px de largura (o dobro do espaço que ocupa no site),
  em WebP qualidade 82, como site/capas/<fid>.webp.

Acontecimentos que já têm capa são pulados, então rodar todo dia só baixa as
fotos dos acontecimentos novos.

Instalação:
    pip install pillow requests

Uso:
    python baixar_capas.py
    python baixar_capas.py --largura 800    # ainda mais nítida (arquivo maior)
"""

import argparse
import io
import os
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from PIL import Image
except ImportError:
    raise SystemExit("Instale com: pip install pillow")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def baixar_original(fid, url):
    for tentativa in range(3):
        try:
            r = requests.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
            if r.status_code != 200:
                return fid, None
            img = Image.open(io.BytesIO(r.content))
            img.load()
            if img.mode in ("P", "LA", "RGBA"):
                img = img.convert("RGBA")
            return fid, img.convert("RGB")
        except Exception:
            time.sleep(1 + tentativa)
    return fid, None


def main():
    ap = argparse.ArgumentParser(description="Baixa as capas dos acontecimentos")
    ap.add_argument("--dados", default="site/dados_site.json")
    ap.add_argument("--pasta", default="site/capas")
    ap.add_argument("--largura", type=int, default=640)
    ap.add_argument("--qualidade", type=int, default=82)
    ap.add_argument("--paralelo", type=int, default=6)
    args = ap.parse_args()

    os.makedirs(args.pasta, exist_ok=True)
    existentes = {f[:-5] for f in os.listdir(args.pasta) if f.endswith(".webp")}
    dados = json.load(open(args.dados, encoding="utf-8"))

    grupos = {}
    for s in dados["subtemas"]:
        lista = [(n["fid"], n["imagem"]) for n in dados["noticias"][s["id"]]
                 if n.get("imagem") and n.get("fid")]
        if lista and not any(fid in existentes for fid, _ in lista):
            grupos[s["id"]] = lista
    if not grupos:
        print(f"Todas as capas já existem ({len(existentes)} na pasta).")
        return
    urls = {fid: url for lista in grupos.values() for fid, url in lista}
    dono = {fid: sid for sid, lista in grupos.items() for fid, _ in lista}
    print(f"{len(grupos)} acontecimentos sem capa, {len(urls)} fotos para comparar...")

    melhor = {}   # sid -> (pixels, fid, imagem)
    feitas = falhas = 0
    with ThreadPoolExecutor(args.paralelo) as pool:
        futuros = [pool.submit(baixar_original, fid, url) for fid, url in urls.items()]
        for f in as_completed(futuros):
            fid, img = f.result()
            feitas += 1
            if img is None:
                falhas += 1
            else:
                sid = dono[fid]
                px = img.width * img.height
                if sid not in melhor or px > melhor[sid][0]:
                    melhor[sid] = (px, fid, img)
            if feitas % 200 == 0 or feitas == len(urls):
                print(f"  [{feitas}/{len(urls)}] falhas: {falhas}")

    for sid, (_px, fid, img) in melhor.items():
        if img.width > args.largura:
            img = img.resize((args.largura, round(img.height * args.largura / img.width)),
                             Image.LANCZOS)
        img.save(os.path.join(args.pasta, f"{fid}.webp"), "WEBP",
                 quality=args.qualidade, method=6)

    print(f"\n{len(melhor)} capas novas em {args.pasta}/")
    if len(grupos) - len(melhor):
        print(f"{len(grupos) - len(melhor)} acontecimentos ficaram sem capa (nenhuma foto baixou).")


if __name__ == "__main__":
    main()
