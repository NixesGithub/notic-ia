#!/usr/bin/env python3
"""Test de la ventana horaria. Sin red: `python3 scripts/test_ventana.py`.

El cron de Actions es UTC y Madrid cambia de offset dos veces al año, así que las
horas locales no se escriben a mano: se calculan desde las horas UTC del workflow
con una fecha de verano y otra de invierno.

El mecanismo se prueba con un margen fijo de 3h (independiente de lo que diga el
workflow). Aparte, se comprueba con el MARGEN_HORAS por defecto real que las horas
en las que GitHub entregó de verdad el `schedule` la primera semana en producción
(29/08-02/09: 12:03, 12:30, 15:00, 12:45, 13:07 y 19:22 UTC, siempre un único
evento al día y nunca dentro de 3 horas del cron) caen dentro de la ventana. Si se
reduce el margen por debajo de lo que GitHub tarda de verdad, este test lo avisa.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


def con_margen(margen):
    """Recarga comun con ese margen. HORA_DIGEST y MARGEN_HORAS se leen al importar."""
    os.environ["DIGEST_MARGEN_HORAS"] = str(margen)
    os.environ.pop("DIGEST_HOUR", None)
    import comun
    return importlib.reload(comun)


def horas_del_cron():
    """Las horas UTC que dispara el workflow, leídas del YAML de verdad."""
    ruta = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "noticias-ia.yml")
    with open(ruta, encoding="utf-8") as f:
        crones = re.findall(r"cron:\s*'([^']+)'", f.read())
    horas = set()
    for cron in crones:
        for parte in cron.split()[1].split(","):
            horas.add(int(parte))
    return sorted(horas)


def locales(horas_utc, fecha):
    """Esas horas UTC, en hora de Madrid, ese día."""
    madrid = ZoneInfo("Europe/Madrid")
    return [datetime(*fecha, h, tzinfo=ZoneInfo("UTC")).astimezone(madrid).hour for h in horas_utc]


ESTACIONES = {"verano (CEST)": (2026, 7, 15), "invierno (CET)": (2026, 1, 15)}


def main() -> int:
    utc = horas_del_cron()
    c = con_margen(3)
    print(f"El workflow dispara a las {utc} UTC; digest a las {c.HORA_DIGEST}:00 "
          f"con {c.MARGEN_HORAS}h de margen.\n")

    comprobar(len(utc) >= 2, f"hay disparos de reserva ({len(utc)} en total)")

    for nombre, fecha in ESTACIONES.items():
        print(f"{nombre}:")
        horas = locales(utc, fecha)
        aceptadas = [h for h in horas if c.en_ventana(h)]
        print(f"  {utc} UTC -> {horas} local; se aceptan {aceptadas}")

        comprobar(bool(aceptadas), "algún disparo cae dentro de la ventana")
        comprobar(aceptadas and min(aceptadas) == c.HORA_DIGEST,
                  f"el primero aceptado es a las {c.HORA_DIGEST}:00, no más tarde")
        comprobar(all(h >= c.HORA_DIGEST for h in aceptadas),
                  "ninguno se acepta antes de la hora del digest")
        # La regresión que motivó todo esto: con la guarda de hora exacta, los
        # disparos posteriores a las 9:00 se descartaban y no había reintento.
        posteriores = [h for h in horas if h > c.HORA_DIGEST]
        comprobar(posteriores and all(c.en_ventana(h) for h in posteriores),
                  f"las reservas {posteriores} se aceptan como reintento")
        rechazadas = [h for h in horas if not c.en_ventana(h)]
        comprobar(all(h < c.HORA_DIGEST for h in rechazadas),
                  f"lo rechazado {rechazadas or '(nada)'} es sólo lo anterior a la hora")
        print()

    print("Bordes de la ventana:")
    comprobar(not c.en_ventana(c.HORA_DIGEST - 1), f"{c.HORA_DIGEST - 1}:00 fuera (demasiado pronto)")
    comprobar(c.en_ventana(c.HORA_DIGEST), f"{c.HORA_DIGEST}:00 dentro")
    comprobar(c.en_ventana(c.HORA_DIGEST + c.MARGEN_HORAS), f"{c.HORA_DIGEST + c.MARGEN_HORAS}:00 dentro (último)")
    comprobar(not c.en_ventana(c.HORA_DIGEST + c.MARGEN_HORAS + 1),
              f"{c.HORA_DIGEST + c.MARGEN_HORAS + 1}:00 fuera (demasiado tarde)")

    print("\nEl margen cubre el último cron:")
    for nombre, fecha in ESTACIONES.items():
        ultima = max(locales(utc, fecha))
        comprobar(c.en_ventana(ultima), f"{nombre}: el último disparo ({ultima}:00) entra")

    print("\nCon DIGEST_MARGEN_HORAS=0 se vuelve a la hora exacta:")
    c0 = con_margen(0)
    comprobar(c0.en_ventana(c0.HORA_DIGEST), "la hora justa se acepta")
    comprobar(not c0.en_ventana(c0.HORA_DIGEST + 1), "una hora después ya no")

    os.environ.pop("DIGEST_MARGEN_HORAS", None)
    import comun
    d = importlib.reload(comun)
    print(f"\nCon el margen por defecto ({d.MARGEN_HORAS}h, sin DIGEST_MARGEN_HORAS puesta):")
    # Horas UTC reales en las que GitHub entregó el `schedule` la semana del
    # 29/08 al 02/09/2026 (runs #8 a #13) — un único evento al día, entre 3 y
    # 12 horas tarde. En CEST (UTC+2) son las 14:03, 14:30, 17:00, 14:45,
    # 15:07 y 21:22 locales. Si esto falla, el margen por defecto volvió a
    # quedarse corto para lo que GitHub tarda de verdad.
    entregas_reales_utc = [12, 12, 15, 12, 13, 19]
    for h_utc in entregas_reales_utc:
        h_local = (h_utc + 2) % 24  # CEST = UTC+2; las seis caídas fueron en verano
        comprobar(d.en_ventana(h_local),
                  f"la entrega real de las {h_utc}:xx UTC ({h_local}:xx local) entra en la ventana")

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
