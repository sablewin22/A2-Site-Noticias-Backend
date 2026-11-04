#!/usr/bin/env python3
"""
Monta o site (site/index.html) a partir do modelo e dos dados.

Junta três coisas:
    site/modelo.html       o site em si (layout, textos, animações)
    site/dados_site.json   os acontecimentos e notícias (exportar_site.py)
    site/capas/*.webp      as capas dos acontecimentos (baixar_capas.py)

O resultado é um site estático: o Vercel só publica a pasta site/.

Uso:
    python montar_site.py
"""

import argparse
import json
import os


def main():
    ap = argparse.ArgumentParser(description="Monta site/index.html")
    ap.add_argument("--pasta", default="site")
    args = ap.parse_args()

    modelo = os.path.join(args.pasta, "modelo.html")
    dados_arq = os.path.join(args.pasta, "dados_site.json")
    capas_dir = os.path.join(args.pasta, "capas")
    for caminho in (modelo, dados_arq):
        if not os.path.exists(caminho):
            raise SystemExit(f"{caminho} não encontrado.")

    dados = json.load(open(dados_arq, encoding="utf-8"))
    arquivos = {f[:-5] for f in os.listdir(capas_dir) if f.endswith(".webp")} \
        if os.path.isdir(capas_dir) else set()
    usadas = {s["capa"] for s in dados["subtemas"] if s.get("capa") in arquivos}
    for s in dados["subtemas"]:
        if s.get("capa") not in arquivos:
            s.pop("capa", None)

    def embutir(obj):
        # "</" dentro de um <script> fecharia a tag antes da hora
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = open(modelo, encoding="utf-8").read()
    for marca in ("__DADOS__", "__FOTOS_IDX__"):
        if html.count(marca) != 1:
            raise SystemExit(f"O modelo precisa ter {marca} exatamente uma vez.")
    html = html.replace("__FOTOS_IDX__", embutir({fid: 1 for fid in sorted(usadas)}))
    html = html.replace("__DADOS__", embutir(dados))

    saida = os.path.join(args.pasta, "index.html")
    with open(saida, "w", encoding="utf-8") as f:
        f.write(html)

    sobrando = arquivos - usadas
    print(f"{saida}: {len(dados['subtemas'])} acontecimentos, {len(usadas)} com capa "
          f"({os.path.getsize(saida) / 1e6:.1f} MB)")
    if sobrando:
        print(f"{len(sobrando)} capas na pasta não são mais usadas "
              f"(acontecimentos que saíram do site).")


if __name__ == "__main__":
    main()
