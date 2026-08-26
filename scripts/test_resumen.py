#!/usr/bin/env python3
"""Test del resumen personal. Sin dependencias ni red: `python3 scripts/test_resumen.py`.

Lo que más importa aquí no es el formato: es que **un día sin nada relevante
produzca cero puntos** y lo diga. Si esto se rompe, el resumen empieza a
rellenar todos los días y deja de servir para distinguir el día que sí importa.
"""

from __future__ import annotations

import sys

import resumen

NOTICIAS = [
    {"titulo": "Sale un modelo nuevo con 1M de contexto", "url": "https://ej.com/a",
     "fuente": "TechCrunch", "extracto": "Detalles del lanzamiento"},
    {"titulo": "Startup de IA levanta 200 millones", "url": "https://ej.com/b",
     "fuente": "VentureBeat", "extracto": "Ronda serie C"},
]

REPOS = [
    {"nombre": "acme/cohete", "url": "https://github.com/acme/cohete", "lenguaje": "Rust",
     "ganadas": 1200, "periodo": "hoy", "descripcion": "Un servidor rápido"},
]

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


def main() -> int:
    print("Un día sin nada relevante:")
    vacio = resumen.formatear_bloques(
        {"puntos": [], "veredicto": "Día sin novedades que cambien nada de tu trabajo."},
        "24 de agosto de 2026",
    )
    comprobar(len(vacio) == 1, f"un solo bloque, sin relleno -> {len(vacio)}")
    comprobar("Día sin novedades" in vacio[0], "el veredicto se muestra")
    comprobar("1." not in vacio[0], "no se numera nada porque no hay nada")

    print("\nCon puntos:")
    datos = {
        "veredicto": "Un cambio real hoy.",
        "puntos": [
            {"titulo": "Modelo con 1M de contexto", "que_cambia": "Pasa de 200k a 1M & más.",
             "que_podes_hacer": "Probá pasarle el repo <entero>.", "faceta": "programador",
             "fuente": "TechCrunch", "url": "https://ej.com/a"},
            {"titulo": "Separador de pistas libre", "que_cambia": "Corre en local.",
             "que_podes_hacer": "Instalalo y separá tus maquetas.", "faceta": "músico",
             "fuente": "GitHub", "url": "https://ej.com/c"},
        ],
    }
    bloques = resumen.formatear_bloques(datos, "24 de agosto de 2026")
    texto = "\n".join(bloques)

    comprobar(len(bloques) == 3, f"cabecera + 2 puntos -> {len(bloques)}")
    comprobar("💻" in texto and "🎸" in texto, "cada faceta lleva su icono")
    comprobar("<b>Qué podés hacer:</b>" in texto, "cada punto dice qué hacer, no sólo qué pasó")
    comprobar("Pasa de 200k a 1M &amp; más." in texto, "el HTML del modelo se escapa")
    comprobar("Probá pasarle el repo &lt;entero&gt;." in texto, "se escapa también en la acción")

    print("\nMaterial de entrada:")
    peticion = resumen.construir_peticion(NOTICIAS, REPOS, "24 de agosto de 2026")
    contenido = peticion["usuario"]
    comprobar("[N1]" in contenido and "[N2]" in contenido, "entran TODAS las noticias candidatas")
    comprobar("Startup de IA levanta 200 millones" in contenido,
              "hasta las que se descartarán: filtra el modelo, no el código")
    comprobar("[R1]" in contenido, "entran los repos")
    comprobar("acme/cohete" in contenido, "con su nombre")
    comprobar("Rondas de financiación" in peticion["system"],
              "el perfil con las exclusiones va en el system prompt")

    print("\nSólo con una de las dos fuentes:")
    solo_noticias = resumen.construir_peticion(NOTICIAS, [], "hoy")["usuario"]
    comprobar("REPOS EN TENDENCIA" not in solo_noticias, "sin repos no se manda esa cabecera vacía")
    solo_repos = resumen.construir_peticion([], REPOS, "hoy")["usuario"]
    comprobar("NOTICIAS" not in solo_repos, "sin noticias tampoco")

    print("\nSin material:")
    try:
        resumen.generar([], [], "hoy", "clave")
        comprobar(False, "sin material debería levantar error")
    except RuntimeError as exc:
        comprobar("No hay material" in str(exc), f"error explícito -> {exc}")

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
