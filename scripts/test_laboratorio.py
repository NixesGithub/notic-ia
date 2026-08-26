#!/usr/bin/env python3
"""Test de la sección de laboratorio. Sin red: `python3 scripts/test_laboratorio.py`.

Lo que sostiene la utilidad de esta sección es que **sin cruce no mande nada**.
Un aviso que llega todos los días deja de ser un aviso, así que ese caso va
primero y es el que no se puede romper.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import laboratorio

NOTICIAS = [
    {"titulo": "GitHub publica una API de repos en tendencia", "url": "https://ej.com/a",
     "fuente": "TechCrunch", "extracto": "Sustituye al scraping de la página"},
]
REPOS = [
    {"nombre": "acme/cohete", "url": "https://github.com/acme/cohete", "lenguaje": "Rust",
     "ganadas": 1200, "periodo": "hoy", "descripcion": "Un servidor rápido"},
]
PROYECTOS = [{"nombre": "notic-ia", "texto": "# notic-ia\n## Bloqueos\n- Raspo HTML frágil."}]

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


def main() -> int:
    print("Sin cruces no se manda nada:")
    vacio = laboratorio.formatear_bloques({"cruces": []}, PROYECTOS, "hoy")
    comprobar(vacio == [], f"cero bloques, ni siquiera una cabecera -> {vacio}")

    print("\nUn proyecto que el modelo se inventó:")
    inventado = laboratorio.formatear_bloques(
        {"cruces": [{"proyecto": "no-existe", "bloqueo": "x", "que_lo_desbloquea": "y",
                     "como_implementarlo": "z", "confianza": "alta",
                     "fuente": "F", "url": "https://ej.com"}]},
        PROYECTOS, "hoy")
    comprobar(inventado == [], "se descarta y, al quedar vacío, tampoco se manda mensaje")

    print("\nCon un cruce real:")
    datos = {"cruces": [{
        "proyecto": "notic-ia",
        "bloqueo": "Raspo HTML frágil de github.com/trending",
        "que_lo_desbloquea": "Hay API oficial con <campos> nuevos & estables",
        "como_implementarlo": "Cambiá _descargar() en scripts/repos.py",
        "confianza": "alta", "fuente": "TechCrunch", "url": "https://ej.com/a"}]}
    bloques = laboratorio.formatear_bloques(datos, PROYECTOS, "24 de agosto de 2026")
    texto = "\n".join(bloques)

    comprobar(len(bloques) == 2, f"cabecera + 1 cruce -> {len(bloques)}")
    comprobar("<b>Bloqueo:</b>" in texto, "dice qué bloqueo ataca")
    comprobar("<b>Cómo meterlo:</b>" in texto, "dice cómo implementarlo, no sólo qué salió")
    comprobar("scripts/repos.py" in texto, "la sugerencia menciona el archivo real del proyecto")
    comprobar("🎯" in texto, "la confianza alta lleva su icono")
    comprobar("&lt;campos&gt; nuevos &amp; estables" in texto, "el HTML del modelo se escapa")

    print("\nCarga del inventario:")
    proyectos = laboratorio.cargar_inventario()
    nombres = [p["nombre"] for p in proyectos]
    comprobar("notic-ia" in nombres, f"lee los .md reales de laboratorio/ -> {nombres}")
    comprobar("README" not in nombres and "PLANTILLA" not in nombres,
              "el README y la plantilla no cuentan como proyectos")
    comprobar(any("Bloqueos" in p["texto"] for p in proyectos),
              "el contenido llega entero, sin parsear")

    with tempfile.TemporaryDirectory() as tmp:
        original = laboratorio.DIRECTORIO
        laboratorio.DIRECTORIO = pathlib.Path(tmp)
        try:
            comprobar(laboratorio.cargar_inventario() == [], "un directorio vacío da lista vacía")
            comprobar(laboratorio.generar(NOTICIAS, REPOS, "hoy", "clave") == [],
                      "sin inventario no se manda mensaje y no se llama al modelo")
            laboratorio.DIRECTORIO = pathlib.Path(tmp) / "no-existe"
            comprobar(laboratorio.cargar_inventario() == [], "un directorio inexistente tampoco rompe")
        finally:
            laboratorio.DIRECTORIO = original

    print("\nEl inventario entra en el prompt:")
    body = laboratorio.construir_body(PROYECTOS, NOTICIAS, REPOS)
    contenido = body["messages"][0]["content"]
    comprobar("INVENTARIO:" in contenido, "va el inventario")
    comprobar("Raspo HTML frágil." in contenido, "con los bloqueos, que son lo que se cruza")
    comprobar("[N1]" in contenido and "[R1]" in contenido, "van las noticias y los repos")
    comprobar("EL SESGO CORRECTO ES NO ENCONTRAR NADA" in body["system"],
              "el prompt dice explícitamente que lo normal es no encontrar nada")

    print("\nSin material:")
    try:
        laboratorio.generar([], [], "hoy", "clave")
        comprobar(False, "sin noticias ni repos debería levantar error")
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
