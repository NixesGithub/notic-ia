#!/usr/bin/env python3
"""Test de la sección de repos con un fixture de la API de búsqueda de GitHub.

Sin dependencias: `python3 scripts/test_repos.py`. Cubre el filtrado, el ranking
por estrellas/día y el formateo del mensaje — que es donde se puede romper algo
en silencio. La llamada HTTP real no se toca: se sustituye `_buscar`.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import repos

AHORA = datetime.now(timezone.utc)


def iso(dias_atras: float) -> str:
    return (AHORA - timedelta(days=dias_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo(nombre, estrellas, edad, empujado=0.5, **extra):
    """Un item de /search/repositories con los campos que consumimos."""
    base = {
        "full_name": nombre,
        "html_url": f"https://github.com/{nombre}",
        "description": f"Descripción de {nombre}",
        "stargazers_count": estrellas,
        "created_at": iso(edad),
        "pushed_at": iso(empujado),
        "language": "Python",
        "topics": ["cli", "ai"],
        "fork": False,
        "archived": False,
    }
    base.update(extra)
    return base


FIXTURE = {
    "nuevos": [
        repo("acme/cohete", 4000, 10),          # 400/día -> el más rápido
        repo("acme/normalito", 300, 10),        # 30/día
        repo("acme/un-fork", 9000, 5, fork=True),
        repo("acme/abandonado", 9000, 5, archived=True),
        repo("acme/muerto", 9000, 5, empujado=60),
        repo("acme/sin-fecha", 9000, 5, created_at=None),
    ],
    "recientes": [
        repo("acme/cohete", 4000, 10),          # duplicado entre consultas
        repo("acme/veterano", 12000, 150),      # 80/día
    ],
}


def fake_buscar(consulta, token, por_pagina=50):
    # Ojo: "stars:>=100" es substring de "stars:>=1000", así que se comprueba
    # primero la consulta más específica.
    return FIXTURE["recientes"] if "stars:>=1000" in consulta else FIXTURE["nuevos"]


fallos = []


def comprobar(condicion, mensaje):
    if condicion:
        print(f"  ok   {mensaje}")
    else:
        print(f"  FALLA {mensaje}")
        fallos.append(mensaje)


def main() -> int:
    repos._buscar = fake_buscar
    candidatos, diag = repos.obtener_candidatos("token-falso")
    nombres = [c["nombre"] for c in candidatos]

    print("Filtrado y ranking:")
    comprobar(nombres == ["acme/cohete", "acme/veterano", "acme/normalito"],
              f"orden por estrellas/día -> {nombres}")
    comprobar(candidatos[0]["estrellas_dia"] == 400.0,
              f"velocidad de cohete = {candidatos[0]['estrellas_dia']}/día")
    comprobar("acme/un-fork" not in nombres, "los forks se descartan")
    comprobar("acme/abandonado" not in nombres, "los archivados se descartan")
    comprobar("acme/muerto" not in nombres, "los inactivos se descartan")
    comprobar("acme/sin-fecha" not in nombres, "los que no traen created_at se descartan")
    comprobar(nombres.count("acme/cohete") == 1, "se deduplica entre consultas")
    comprobar(diag == {"devueltos": 8, "fork": 1, "archivado": 1, "inactivo": 1,
                       "sinFecha": 1, "ok": 3, "consultasFallidas": 0},
              f"diagnóstico -> {diag}")

    print("\nFormato del mensaje:")
    respuesta_modelo = {
        "resumen_global": "Todo apunta a agentes.",
        "repos": [
            {"nombre": "acme/cohete", "para_que_sirve": "Hace <cosas> & más.",
             "por_que_sube": "Salió en Hacker News.", "categoria": "herramienta CLI"},
            {"nombre": "acme/inventado", "para_que_sirve": "No existe.",
             "por_que_sube": "No existe.", "categoria": "fantasma"},
        ],
    }
    bloques = repos.formatear_bloques(respuesta_modelo, candidatos, "24 de agosto de 2026")
    texto = "\n".join(bloques)

    comprobar(len(bloques) == 2, f"cabecera + 1 repo (el inventado se cae) -> {len(bloques)} bloques")
    comprobar("acme/inventado" not in texto, "un repo que el modelo inventó no llega al mensaje")
    comprobar("⭐ 4,0k" in texto, "las estrellas salen de la búsqueda, no del modelo")
    comprobar("400.0/día" in texto, "el ritmo real aparece en el mensaje")
    comprobar("Hace &lt;cosas&gt; &amp; más." in texto, "el HTML del modelo se escapa")
    comprobar("<b>Para qué sirve:</b>" in texto, "se explica para qué sirve la herramienta")

    print("\nSin candidatos:")
    vacio = repos.formatear_bloques({"resumen_global": "", "repos": []}, [], "hoy")
    comprobar(len(vacio) == 1 and "No se encontraron" in vacio[0],
              "mensaje de relleno cuando no hay nada")

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
