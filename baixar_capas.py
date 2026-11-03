#!/usr/bin/env python3
"""
Baixa a foto de capa de cada acontecimento em boa qualidade.

Para cada acontecimento do dados_site.json:
- baixa a foto ORIGINAL de cada notícia do grupo;
- escolhe a de maior resolução real (mais pixels);
- salva essa foto com até 640 px de largura (o dobro do espaço que ela ocupa
  no site, para ficar nítida em telas de alta resolução), em WebP qualidade 82.

Gera capas.zip, com uma foto por acontecimento (<fid>.webp) e um arquivo
capas.json com a resolução original de cada uma.

Instalação:
    pip install pillow requests

Uso:
    python baixar_capas.py
    python baixar_capas.py --largura 800    # ainda mais nítida (arquivo maior)
"""

import argparse
import io
import json
import os
import time
import zipfile
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
            return fid, img.convert("RGB")
        except Exception:
            time.sleep(1 + tentativa)
    return fid, None


def main():
    ap = argparse.ArgumentParser(description="Baixa as capas dos acontecimentos em boa qualidade")
    ap.add_argument("--dados", default="dados_site.json")
    ap.add_argument("--saida", default="capas.zip")
    ap.add_argument("--largura", type=int, default=640)
    ap.add_argument("--qualidade", type=int, default=82)
    ap.add_argument("--paralelo", type=int, default=6)
    args = ap.parse_args()

    dados = json.load(open(args.dados, encoding="utf-8"))
    grupos = {s["id"]: [(n["fid"], n["imagem"]) for n in dados["noticias"][s["id"]]
                        if n.get("imagem") and n.get("fid")]
              for s in dados["subtemas"]}
    urls = {fid: url for lista in grupos.values() for fid, url in lista}
    print(f"{len(grupos)} acontecimentos, {len(urls)} fotos originais para comparar...")

    # baixa todas as originais, guardando só a resolução e a imagem da melhor de cada grupo
    dono = {fid: sid for sid, lista in grupos.items() for fid, _ in lista}
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

    resolucao = {}
    with zipfile.ZipFile(args.saida, "w", zipfile.ZIP_STORED) as z:
        for sid, (px, fid, img) in melhor.items():
            resolucao[fid] = [img.width, img.height]
            if img.width > args.largura:
                img = img.resize((args.largura, round(img.height * args.largura / img.width)),
                                 Image.LANCZOS)
            saida = io.BytesIO()
            img.save(saida, "WEBP", quality=args.qualidade, method=6)
            z.writestr(f"{fid}.webp", saida.getvalue())
        z.writestr("capas.json", json.dumps(resolucao))

    pequenas = sum(1 for w, _ in resolucao.values() if w < args.largura)
    print(f"\n{len(melhor)} capas em {args.saida} ({os.path.getsize(args.saida) / 1e6:.1f} MB)")
    print(f"{len(grupos) - len(melhor)} acontecimentos ficaram sem capa (nenhuma foto baixou).")
    if pequenas:
        print(f"{pequenas} capas têm original menor que {args.largura} px "
              f"(foram mantidas no tamanho original).")


if __name__ == "__main__":
    main()
