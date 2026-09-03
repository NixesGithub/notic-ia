#!/usr/bin/env python3
"""Test del envío a Telegram. Sin red: `python3 scripts/test_telegram.py`.

El 27/08 un read timeout en el primer mensaje mató la sección de noticias entera:
`enviar_telegram` sólo capturaba HTTPError, así que cualquier fallo de red subía
y se llevaba la sección por delante. Aquí se comprueba que ahora reintenta lo que
es transitorio y se rinde rápido con lo que no lo es.
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.request

import comun

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def http(codigo, cuerpo=b"{}"):
    return urllib.error.HTTPError("u", codigo, "err", {}, io.BytesIO(cuerpo))


def simular(reacciones):
    """Cada llamada a urlopen devuelve o lanza la siguiente reacción de la lista."""
    registro = {"llamadas": 0, "esperas": [], "cuerpos": []}

    def urlopen(peticion, timeout=None):
        registro["llamadas"] += 1
        registro["cuerpos"].append(peticion.data)
        reaccion = reacciones[min(registro["llamadas"] - 1, len(reacciones) - 1)]
        if isinstance(reaccion, BaseException):
            raise reaccion
        return Respuesta(b'{"ok":true}')

    comun.urllib.request.urlopen = urlopen
    comun.time.sleep = lambda s: registro["esperas"].append(s)
    return registro


def restaurar():
    import time
    comun.urllib.request.urlopen = urllib.request.urlopen
    comun.time.sleep = time.sleep


def main() -> int:
    print("Un read timeout se reintenta (el fallo del 27/08):")
    r = simular([TimeoutError("The read operation timed out"), None])
    comun.enviar_telegram(["hola"], "t", "c")
    comprobar(r["llamadas"] == 2, f"reintentó y acabó enviando ({r['llamadas']} llamadas)")
    comprobar(r["esperas"] == [2], f"esperó antes de reintentar -> {r['esperas']}")

    print("\nNo se pierden los mensajes siguientes:")
    r = simular([TimeoutError("timeout"), None, None])
    comun.enviar_telegram(["uno", "dos"], "t", "c")
    comprobar(r["llamadas"] == 3, f"1 reintento + 2 envíos ({r['llamadas']} llamadas)")
    enviados = [c for c in r["cuerpos"] if b'"uno"' in c or b'"dos"' in c]
    comprobar(sum(b'"dos"' in c for c in r["cuerpos"]) == 1, "el segundo mensaje llegó a salir")
    comprobar(len(enviados) == 3, "ningún mensaje se saltó")

    print("\nUn error nuestro no se reintenta:")
    for codigo, motivo in [(400, "HTML inválido"), (401, "token mal"), (403, "chat_id mal")]:
        r = simular([http(codigo, b'{"description":"x"}')])
        try:
            comun.enviar_telegram(["hola"], "t", "c")
            comprobar(False, f"{codigo} debería fallar")
        except RuntimeError as e:
            comprobar(r["llamadas"] == 1 and str(codigo) in str(e),
                      f"{codigo} ({motivo}): se rinde a la primera")

    print("\nLo transitorio del servidor sí se reintenta:")
    r = simular([http(500), http(502), None])
    comun.enviar_telegram(["hola"], "t", "c")
    comprobar(r["llamadas"] == 3, f"reintentó los 5xx ({r['llamadas']} llamadas)")

    r = simular([http(429, b'{"parameters":{"retry_after":7}}'), None])
    comun.enviar_telegram(["hola"], "t", "c")
    comprobar(r["llamadas"] == 2, "el rate limit se reintenta")
    comprobar(r["esperas"] == [7], f"respeta el retry_after de Telegram -> {r['esperas']}")

    r = simular([http(429, b"no es json"), None])
    comun.enviar_telegram(["hola"], "t", "c")
    comprobar(r["esperas"] == [2], f"sin retry_after usable, backoff normal -> {r['esperas']}")

    print("\nSi no hay manera, avisa:")
    r = simular([TimeoutError("timeout")])
    try:
        comun.enviar_telegram(["hola"], "t", "c")
        comprobar(False, "debería acabar lanzando")
    except RuntimeError as e:
        comprobar(r["llamadas"] == 3, f"agotó los 3 intentos ({r['llamadas']})")
        comprobar("timeout" in str(e), f"dice qué pasó -> {str(e)[:50]}")

    restaurar()
    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
