#!/usr/bin/env python3
"""Test del cambio de proveedor. Sin red: `python3 scripts/test_proveedor.py`.

El proveedor se elige con LLM_BASE_URL, y de ahí salen dos formatos distintos de
petición y de respuesta. Lo que se comprueba es que el esquema JSON viaja en los
dos —es la pieza de la que depende todo el pipeline— y que el system acaba donde
cada API lo espera.
"""

from __future__ import annotations

import importlib
import os
import sys

fallos = []


def comprobar(condicion, mensaje):
    print(f"  {'ok  ' if condicion else 'FALLA'} {mensaje}")
    if not condicion:
        fallos.append(mensaje)


# Las variables del proveedor se leen al importar (BASE_URL), pero el modelo se
# lee en cada llamada. Así que NO se puede restaurar el entorno antes de
# comprobar: hay que dejarlo puesto mientras se mira. Cada llamada fija el juego
# completo, y main() limpia al final.
VARIABLES = ("LLM_BASE_URL", "DIGEST_MODEL", "DIGEST_MODEL_RESUMEN", "LLM_API_KEY")


def con_entorno(**variables):
    """Deja el entorno pedido y recarga comun. Lo no pedido se borra."""
    for k in VARIABLES:
        valor = variables.get(k)
        if valor is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = valor
    import comun
    return importlib.reload(comun)


ESQUEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def main() -> int:
    print("Anthropic (por defecto):")
    c = con_entorno(LLM_BASE_URL=None, DIGEST_MODEL=None, LLM_API_KEY="k")
    url, cab, cuerpo = c._peticion("claude-sonnet-5", "SYS", "USR", ESQUEMA, "k")
    comprobar(url.endswith("/v1/messages"), f"endpoint -> {url}")
    comprobar(cab.get("x-api-key") == "k" and "anthropic-version" in cab, "auth por x-api-key + versión")
    comprobar(cuerpo["system"] == "SYS", "el system va en su campo propio")
    comprobar(cuerpo["output_config"]["format"]["schema"] is ESQUEMA, "el esquema viaja")
    comprobar(c.modelo_de("resumen") == "claude-sonnet-5", "modelo por defecto")

    print("\nCompatible con OpenAI (OpenRouter, Groq, Kimi...):")
    c = con_entorno(LLM_BASE_URL="https://openrouter.ai/api", DIGEST_MODEL="un/modelo:free", LLM_API_KEY="k")
    url, cab, cuerpo = c._peticion("un/modelo:free", "SYS", "USR", ESQUEMA, "k")
    comprobar(url.endswith("/v1/chat/completions"), f"endpoint -> {url}")
    comprobar(cab.get("authorization") == "Bearer k", "auth por Bearer")
    comprobar(cuerpo["messages"][0] == {"role": "system", "content": "SYS"},
              "el system pasa a ser un mensaje")
    comprobar(cuerpo["messages"][1]["content"] == "USR", "y el usuario va detrás")
    comprobar(cuerpo["response_format"]["json_schema"]["schema"] is ESQUEMA, "el esquema viaja")
    comprobar(cuerpo["response_format"]["json_schema"]["strict"] is True,
              "con strict: sin eso el JSON llega aproximado y extraer falla")

    print("\nModelo por sección:")
    c = con_entorno(LLM_BASE_URL="https://openrouter.ai/api", DIGEST_MODEL="barato:free",
                    DIGEST_MODEL_RESUMEN="bueno", LLM_API_KEY="k")
    comprobar(c.modelo_de("noticias") == "barato:free", "lo mecánico usa el general")
    comprobar(c.modelo_de("resumen") == "bueno", "el juicio usa su override")

    print("\nLectura de la respuesta:")
    c = con_entorno(LLM_BASE_URL=None, DIGEST_MODEL=None, LLM_API_KEY="k")
    texto, uso = c._leer({"content": [{"type": "text", "text": "{}"}],
                          "usage": {"input_tokens": 10, "output_tokens": 2}, "model": "m"})
    comprobar(texto == "{}" and "entrada 10" in uso, f"Anthropic -> {uso}")
    try:
        c._leer({"stop_reason": "refusal", "stop_details": {"explanation": "no"}})
        comprobar(False, "un rechazo de Anthropic debería levantar error")
    except RuntimeError as e:
        comprobar("rechazó" in str(e), "un rechazo levanta error")

    c = con_entorno(LLM_BASE_URL="https://openrouter.ai/api", DIGEST_MODEL="m", LLM_API_KEY="k")
    texto, uso = c._leer({"choices": [{"message": {"content": "{}"}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 2}, "model": "m"})
    comprobar(texto == "{}" and "entrada 10" in uso, f"OpenAI -> {uso}")
    try:
        c._leer({"choices": [{"message": {"refusal": "no puedo"}}]})
        comprobar(False, "un rechazo compatible debería levantar error")
    except RuntimeError as e:
        comprobar("rechazó" in str(e), "un rechazo levanta error también aquí")

    print("\nSin modelo configurado:")
    c = con_entorno(LLM_BASE_URL="https://openrouter.ai/api", DIGEST_MODEL=None, LLM_API_KEY="k")
    comprobar(c.modelo_de("noticias") == "", "fuera de Anthropic no hay modelo por defecto")
    try:
        c.preguntar("noticias", "s", "u", ESQUEMA, clave="k")
        comprobar(False, "debería avisar de que falta DIGEST_MODEL")
    except RuntimeError as e:
        comprobar("DIGEST_MODEL" in str(e), f"error explícito -> {str(e)[:60]}")

    for k in VARIABLES:
        os.environ.pop(k, None)

    print()
    if fallos:
        print(f"{len(fallos)} comprobación(es) fallida(s).")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
