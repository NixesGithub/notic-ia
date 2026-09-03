#!/usr/bin/env python3
"""Test de la sección de repos. Sin dependencias ni red: `python3 scripts/test_repos.py`.

OJO con el alcance de esto: el HTML de abajo está **reconstruido a mano** a
partir de la estructura conocida de github.com/trending, no capturado de la
página real. O sea que estas comprobaciones demuestran que el parser aguanta esa
estructura y sus huecos — no que la estructura siga siendo la de hoy. Lo que
detecta un cambio de marcado en producción es el error que levanta `generar()`
cuando no extrae ni un repo, y el diagnóstico del log.

Si alguna vez falla en producción: guardá el HTML real, pegalo aquí como fixture
y arreglá `parsear()` contra él. Ya pasó una vez: el fixture no llevaba el icono
<svg class="octicon octicon-star"> dentro del enlace de stargazers, y por eso el
test daba verde mientras en producción el total de estrellas salía siempre 0.
"""

from __future__ import annotations

import sys
import urllib.error

import repos

# Una fila normal, con todo: enlace a stargazers, lenguaje, descripción y
# "stars today". El <svg> anidado está a propósito, para que el troceado por
# <article> no se confunda.
FILA_COMPLETA = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/acme/cohete">
      <svg aria-hidden="true"><path d="M8 0"></path></svg>
      <span class="text-normal">acme /</span>
      cohete
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">
    Un servidor de &lt;cosas&gt; r&aacute;pido &amp; peque&ntilde;o
  </p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block ml-0 mr-3">
      <span class="repo-language-color"></span>
      <span itemprop="programmingLanguage">Rust</span>
    </span>
    <a href="/acme/cohete/stargazers" class="Link--muted d-inline-block mr-3">
      <svg aria-hidden="true" class="octicon octicon-star"><path d="M8 .25a.75"></path></svg>
      12,345
    </a>
    <a href="/acme/cohete/forks" class="Link--muted d-inline-block mr-3">678</a>
    <span class="d-inline-block float-sm-right">1,234 stars today</span>
  </div>
</article>
"""

FILA_SIN_DESCRIPCION_NI_LENGUAJE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/acme/pelado">acme / pelado</a></h2>
  <div class="f6 color-fg-muted mt-2">
    <a href="/acme/pelado/stargazers" class="Link--muted d-inline-block mr-3"><svg class="octicon octicon-star"></svg> 890</a>
    <span class="d-inline-block float-sm-right">45 stars today</span>
  </div>
</article>
"""

# Sin enlace a stargazers: el nombre tiene que salir del <h2>.
FILA_SOLO_H2 = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/acme/sin-contador">acme / sin-contador</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Sin contador de estrellas</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block float-sm-right">7 stars today</span>
  </div>
</article>
"""

FILA_SIN_GANADAS = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/acme/quieto">acme / quieto</a></h2>
  <div class="f6 color-fg-muted mt-2">
    <a href="/acme/quieto/stargazers" class="Link--muted d-inline-block mr-3"><svg class="octicon octicon-star"></svg> 2,000</a>
  </div>
</article>
"""

FILA_INSERVIBLE = """
<article class="Box-row"><div>marcado que no reconocemos</div></article>
"""

PAGINA_DAILY = (
    "<html><body><main>"
    + FILA_COMPLETA + FILA_SIN_DESCRIPCION_NI_LENGUAJE + FILA_SOLO_H2
    + FILA_SIN_GANADAS + FILA_INSERVIBLE
    + "</main></body></html>"
)

# weekly repite acme/cohete (debe ganar daily) y trae uno nuevo.
FILA_SEMANAL = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/acme/constante">acme / constante</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Sube despacio pero sin parar</p>
  <div class="f6 color-fg-muted mt-2">
    <a href="/acme/constante/stargazers" class="Link--muted d-inline-block mr-3"><svg class="octicon octicon-star"></svg> 5,000</a>
    <span class="d-inline-block float-sm-right">2,100 stars this week</span>
  </div>
</article>
"""
PAGINA_WEEKLY = "<html><body>" + FILA_COMPLETA + FILA_SEMANAL + "</body></html>"

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


def main() -> int:
    print("Parseo de una página:")
    encontrados, diag = repos.parsear(PAGINA_DAILY)
    por_nombre = {r["nombre"]: r for r in encontrados}

    comprobar(diag["articulos"] == 5, f"cuenta las 5 filas -> {diag['articulos']}")
    comprobar(diag["ok"] == 4, f"extrae 4 (la inservible se descarta) -> {diag['ok']}")
    comprobar(diag["sinNombre"] == 1, f"contabiliza la fila sin nombre -> {diag['sinNombre']}")
    comprobar(diag["sinGanadas"] == 1, f"contabiliza la fila sin estrellas ganadas -> {diag['sinGanadas']}")
    comprobar(diag["sinTotal"] == 2, f"contabiliza las filas sin total -> {diag['sinTotal']}")
    comprobar(diag["sinLenguaje"] == 3, f"contabiliza las filas sin lenguaje -> {diag['sinLenguaje']}")
    comprobar(diag["sinDescripcion"] == 2, f"contabiliza las filas sin descripción -> {diag['sinDescripcion']}")

    cohete = por_nombre.get("acme/cohete", {})
    comprobar(cohete.get("ganadas") == 1234, f"'1,234 stars today' -> {cohete.get('ganadas')}")
    comprobar(cohete.get("estrellas") == 12345,
              f"total con coma de millares, con el icono <svg> por medio -> {cohete.get('estrellas')}")
    comprobar(por_nombre.get("acme/pelado", {}).get("estrellas") == 890,
              "el total se lee aunque el icono y el número estén en la misma línea")
    comprobar(cohete.get("lenguaje") == "Rust", f"lenguaje -> {cohete.get('lenguaje')!r}")
    comprobar(cohete.get("descripcion") == "Un servidor de <cosas> rápido & pequeño",
              f"descripción con entidades y saltos de línea -> {cohete.get('descripcion')!r}")
    comprobar(cohete.get("url") == "https://github.com/acme/cohete", "url construida")
    comprobar("acme/sin-contador" in por_nombre, "sin enlace de stargazers, el nombre sale del h2")
    comprobar(por_nombre.get("acme/sin-contador", {}).get("estrellas") == 0, "total desconocido queda en 0")
    comprobar(por_nombre.get("acme/quieto", {}).get("ganadas") == 0, "sin 'stars today' queda en 0")

    print("\nOrden y deduplicación entre periodos:")
    paginas = {"daily": PAGINA_DAILY, "weekly": PAGINA_WEEKLY}
    repos._descargar = lambda periodo: paginas[periodo]
    candidatos, diag_total = repos.obtener_candidatos()
    nombres = [c["nombre"] for c in candidatos]

    comprobar(nombres[:4] == ["acme/cohete", "acme/pelado", "acme/sin-contador", "acme/quieto"],
              f"se respeta el orden de GitHub, daily primero -> {nombres}")
    comprobar(nombres.count("acme/cohete") == 1, "un repo en las dos páginas aparece una vez")
    comprobar(candidatos[0]["periodo"] == "hoy", "daily gana sobre weekly en el duplicado")
    comprobar(por_nombre_de(candidatos, "acme/constante")["periodo"] == "esta semana",
              "el exclusivo de weekly conserva su periodo")
    comprobar(por_nombre_de(candidatos, "acme/constante")["ganadas"] == 2100,
              "'2,100 stars this week' se lee bien")
    comprobar(diag_total["fallos"] == 0, "sin fallos de descarga")

    print("\nFormato del mensaje:")
    respuesta_modelo = {
        "resumen_global": "Todo apunta a agentes.",
        "repos": [
            {"nombre": "acme/cohete", "para_que_sirve": "Sirve <cosas> & más.",
             "por_que_sube": "Salió en Hacker News.", "categoria": "servidor"},
            {"nombre": "acme/inventado", "para_que_sirve": "No existe.",
             "por_que_sube": "No existe.", "categoria": "fantasma"},
        ],
    }
    bloques = repos.formatear_bloques(respuesta_modelo, candidatos, "24 de agosto de 2026")
    texto = "\n".join(bloques)

    comprobar(len(bloques) == 2, f"cabecera + 1 repo (el inventado se cae) -> {len(bloques)}")
    comprobar("acme/inventado" not in texto, "un repo inventado por el modelo no llega al mensaje")
    comprobar("+1,2k ⭐ hoy" in texto, f"la cifra de GitHub se muestra tal cual")
    comprobar("12,3k en total" in texto, "el total también sale de GitHub")
    comprobar("Sirve &lt;cosas&gt; &amp; más." in texto, "el HTML del modelo se escapa")
    comprobar("<b>Para qué sirve:</b>" in texto, "se explica para qué sirve la herramienta")

    print("\nFallos que tienen que ser ruidosos:")
    repos._descargar = lambda periodo: "<html><body>página sin artículos</body></html>"
    try:
        repos.generar("hoy", "clave", solo_candidatos=True)
        comprobar(False, "un marcado irreconocible debería levantar error")
    except RuntimeError as exc:
        comprobar("cambió el marcado" in str(exc), f"error explícito de marcado -> {exc}")

    def explota(periodo):
        raise urllib.error.URLError("sin red")

    repos._descargar = explota
    try:
        repos.generar("hoy", "clave", solo_candidatos=True)
        comprobar(False, "sin descarga debería levantar error")
    except RuntimeError as exc:
        comprobar("No se pudo descargar" in str(exc), f"error explícito de descarga -> {exc}")

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


def por_nombre_de(candidatos, nombre):
    return next((c for c in candidatos if c["nombre"] == nombre), {})


if __name__ == "__main__":
    sys.exit(main())
