#!/usr/bin/env python3
"""Test estructural de la lista de fuentes. Sin red: `python3 scripts/test_fuentes.py`.

No comprueba que los feeds respondan —eso necesita internet y cambia solo—, sino
que la lista esté bien formada. El fallo que persigue es el silencioso: añadir
una fuente y olvidarse del peso, con lo que entra valiendo lo mínimo y el corte
la expulsa siempre. Una fuente que nunca aparece parece un feed pobre, no un
error de configuración.
"""

from __future__ import annotations

import sys
from datetime import datetime

import noticias_ia as n

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


def main() -> int:
    fuentes = n.construir_fuentes(datetime(2026, 8, 25))

    print("Forma de la lista:")
    comprobar(len(fuentes) >= 20, f"hay fuentes suficientes -> {len(fuentes)}")
    comprobar(all({"fuente", "peso", "url"} <= set(f) for f in fuentes),
              "todas declaran fuente, peso y url")
    comprobar(all(isinstance(f["peso"], int) and 1 <= f["peso"] <= 5 for f in fuentes),
              "todos los pesos son enteros de 1 a 5")

    urls = [f["url"] for f in fuentes]
    comprobar(len(urls) == len(set(urls)), "no hay URLs repetidas")
    nombres = [f["fuente"] for f in fuentes]
    comprobar(len(nombres) == len(set(nombres)), "no hay nombres repetidos")
    comprobar(all(f["url"].startswith("https://") for f in fuentes), "todas por HTTPS")

    print("\nTemáticas y cupos:")
    tematicas = {f.get("tematica", "ia") for f in fuentes}
    comprobar(tematicas == {"ia", "musica", "fabricacion"}, f"temáticas conocidas -> {tematicas}")
    for tema, (suelo, techo) in n.CUPOS.items():
        disponibles = sum(1 for f in fuentes if f.get("tematica") == tema)
        comprobar(disponibles >= suelo,
                  f"'{tema}' tiene {disponibles} fuentes para un suelo de {suelo}")
        comprobar(suelo <= techo, f"'{tema}': el suelo ({suelo}) no supera al techo ({techo})")

    print("\nPrimarias por encima de la prensa:")
    peso_de = {f["fuente"]: f["peso"] for f in fuentes}
    for primaria in ("OpenAI", "Anthropic", "Google DeepMind", "Hugging Face"):
        comprobar(peso_de.get(primaria, 0) > peso_de.get("TechCrunch", 9),
                  f"{primaria} ({peso_de.get(primaria)}) pesa más que TechCrunch ({peso_de.get('TechCrunch')})")
    comprobar(all(p == 5 for f, p in peso_de.items() if f.startswith("release:")),
              "las releases de herramientas propias son lo más pesado")

    print("\nVentana de fechas en las consultas de Google News:")
    gnews = [f["url"] for f in fuentes if "news.google.com" in f["url"]]
    comprobar(len(gnews) >= 4, f"hay varias consultas de Google News -> {len(gnews)}")
    comprobar(all("after%3A2026-08-24" in u and "before%3A2026-08-26" in u for u in gnews),
              "todas acotan al día de referencia (sin eso Google devuelve sólo hoy)")

    def cuenta(seleccion):
        return {t: sum(1 for c in seleccion if c["tematica"] == t)
                for t in ("ia", "musica", "fabricacion")}

    print("\nSuelo: una temática que el ranking dejaría fuera entra igual")
    ordenados = (
        [{"score": 100 - i, "tematica": "ia"} for i in range(10)]
        + [{"score": 5, "tematica": "musica"}, {"score": 4, "tematica": "musica"},
           {"score": 3, "tematica": "fabricacion"}]
    )
    seleccion = n.aplicar_cupos(ordenados, 5, {"musica": (2, 4), "fabricacion": (1, 4)})
    c = cuenta(seleccion)
    comprobar(len(seleccion) == 5, f"el tamaño del corte no cambia -> {len(seleccion)}")
    comprobar(c["musica"] == 2 and c["fabricacion"] == 1, f"se respeta el suelo -> {c}")
    comprobar(c["ia"] == 2, "se sacrifican las de IA peor puntuadas, no las mejores")
    comprobar([x["score"] for x in seleccion] == sorted((x["score"] for x in seleccion), reverse=True),
              "la selección sale ordenada por score")

    print("\nTecho: una fuente prolífica no se come el corte")
    prolifica = (
        [{"score": 50 - i, "tematica": "fabricacion"} for i in range(8)]
        + [{"score": 20 - i, "tematica": "ia"} for i in range(8)]
    )
    seleccion = n.aplicar_cupos(prolifica, 10, {"musica": (0, 4), "fabricacion": (2, 3)})
    c = cuenta(seleccion)
    comprobar(c["fabricacion"] == 3, f"se recorta al techo aunque puntúen más -> {c}")
    comprobar(c["ia"] == 7, "los huecos liberados los ocupa la temática general")
    comprobar(len(seleccion) == 10, f"el tamaño no cambia -> {len(seleccion)}")

    print("\nCasos límite:")
    sin_tema = [{"score": 10 - i, "tematica": "ia"} for i in range(8)]
    comprobar(len(n.aplicar_cupos(sin_tema, 5, {"musica": (2, 4)})) == 5,
              "si no hay candidatos de esa temática, no se rompe ni se encoge")
    solo_tema = [{"score": 10 - i, "tematica": "musica"} for i in range(3)]
    comprobar(len(n.aplicar_cupos(solo_tema, 5, {"musica": (2, 4)})) == 3,
              "sin nada de IA que sacrificar, tampoco rompe")

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
