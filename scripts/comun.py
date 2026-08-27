"""Piezas compartidas por las secciones del digest (noticias, repos).

Todo lo que necesita más de una sección vive aquí: la zona horaria, la llamada
a Anthropic, el troceado y el envío a Telegram.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# `or` y no get(..., default): Actions inyecta la variable VACÍA cuando no
# está definida, y entonces el default de get() nunca se aplica.
ZONA = ZoneInfo(os.environ.get("DIGEST_TZ") or "Europe/Madrid")
HORA_DIGEST = int(os.environ.get("DIGEST_HOUR") or "9")
# GitHub descarta ejecuciones programadas cuando la plataforma va cargada, así que
# el workflow dispara varias veces y aceptamos el digest dentro de una ventana en
# vez de exigir la hora exacta. Quien impide el envío doble es la marca de caché.
MARGEN_HORAS = int(os.environ.get("DIGEST_MARGEN_HORAS") or "3")

# Muchos servidores rechazan el User-Agent por defecto de urllib con un 403.
AGENTE = "Mozilla/5.0 (compatible; notic-ia/1.0; +https://github.com/NixesGithub/notic-ia)"

LIMITE_TELEGRAM = 3800  # Telegram corta a 4096; dejamos margen.

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def log(mensaje: str) -> None:
    print(mensaje, flush=True)


def normalizar(texto: str | None) -> str:
    """Quita acentos y puntuación para poder comparar cadenas entre fuentes."""
    base = unicodedata.normalize("NFD", texto or "").lower()
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = re.sub(r"[^a-z0-9 ]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def limpiar_html(texto: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", texto or "")).strip()


def escapar(texto) -> str:
    """Telegram en modo HTML sólo permite unas pocas etiquetas: escapamos el resto."""
    return (
        str(texto if texto is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def fecha_en_espanol(dia: datetime) -> str:
    return f"{dia.day} de {MESES_ES[dia.month - 1]} de {dia.year}"


def en_ventana(hora: int) -> bool:
    """¿Esa hora local es una a la que el digest todavía puede salir?

    La hora del digest es el único momento *deseable*; el margen existe sólo para
    recuperar los disparos que GitHub descarta o retrasa. Nunca antes de la hora
    pedida: un digest de las 9 no puede salir a las 8.
    """
    return HORA_DIGEST <= hora <= HORA_DIGEST + MARGEN_HORAS


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# El modelo
#
# El proveedor se elige con LLM_BASE_URL. Por defecto Anthropic; cualquier otra
# URL se trata como API compatible con OpenAI, que es lo que hablan OpenRouter,
# Groq, Cerebras, Moonshot (Kimi) y prácticamente todos los demás. Son dos
# formatos de petición y dos de respuesta, y nada más: el resto del código pide
# "esto es el system, esto el mensaje, esto el esquema" y no sabe quién contesta.
# --------------------------------------------------------------------------

BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.anthropic.com").rstrip("/")
ES_ANTHROPIC = "api.anthropic.com" in BASE_URL

MAX_TOKENS = 16000


def clave_api() -> str:
    """LLM_API_KEY manda; ANTHROPIC_API_KEY se acepta por compatibilidad."""
    return os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")


def modelo_de(seccion: str) -> str:
    """El modelo de una sección, con override propio.

    DIGEST_MODEL_RESUMEN gana sobre DIGEST_MODEL. Existe porque las secciones no
    piden lo mismo: resumir titulares es mecánico y lo hace cualquier modelo,
    mientras que decidir qué te cambia algo —y atreverse a no devolver nada— es
    justo donde se nota la diferencia. Así se puede poner uno gratis en lo
    mecánico y reservar el bueno para el juicio.
    """
    return (
        os.environ.get(f"DIGEST_MODEL_{seccion.upper()}")
        or os.environ.get("DIGEST_MODEL")
        or ("claude-sonnet-5" if ES_ANTHROPIC else "")
    )


def _peticion(modelo: str, system: str, usuario: str, esquema: dict, clave: str):
    """Devuelve (url, cabeceras, cuerpo) en el formato del proveedor activo."""
    if ES_ANTHROPIC:
        return (
            f"{BASE_URL}/v1/messages",
            {
                "content-type": "application/json",
                "x-api-key": clave,
                # La credencial sólo inyecta x-api-key: la versión va aparte.
                "anthropic-version": "2023-06-01",
            },
            {
                "model": modelo,
                "max_tokens": MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": usuario}],
                "output_config": {"format": {"type": "json_schema", "schema": esquema}},
            },
        )

    # Compatible con OpenAI: el system es un mensaje más y el esquema va en
    # response_format, con strict para que no devuelva JSON aproximado.
    return (
        f"{BASE_URL}/v1/chat/completions",
        {"content-type": "application/json", "authorization": f"Bearer {clave}"},
        {
            "model": modelo,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": usuario},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "respuesta", "schema": esquema, "strict": True},
            },
        },
    )


def _leer(respuesta: dict) -> tuple[str, str]:
    """Devuelve (texto, resumen de uso). Levanta si el modelo se negó."""
    if ES_ANTHROPIC:
        if respuesta.get("stop_reason") == "refusal":
            detalle = (respuesta.get("stop_details") or {}).get("explanation", "sin detalle")
            raise RuntimeError(f"El modelo rechazó la petición: {detalle}")
        bloque = next(
            (b for b in respuesta.get("content", []) if b.get("type") == "text"), None
        )
        if not bloque:
            raise RuntimeError(
                "La respuesta no contiene ningún bloque de texto: "
                + json.dumps(respuesta)[:500]
            )
        uso = respuesta.get("usage", {})
        entrada, salida = uso.get("input_tokens", "?"), uso.get("output_tokens", "?")
        texto = bloque["text"]
    else:
        opciones = respuesta.get("choices") or []
        if not opciones:
            raise RuntimeError("La respuesta no trae choices: " + json.dumps(respuesta)[:500])
        mensaje = opciones[0].get("message") or {}
        if mensaje.get("refusal"):
            raise RuntimeError(f"El modelo rechazó la petición: {mensaje['refusal']}")
        texto = mensaje.get("content")
        if not texto:
            raise RuntimeError(
                "La respuesta no trae contenido: " + json.dumps(respuesta)[:500]
            )
        uso = respuesta.get("usage", {})
        entrada, salida = uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?")

    return texto, f"entrada {entrada} / salida {salida} | modelo {respuesta.get('model')}"


def preguntar(
    seccion: str, system: str, usuario: str, esquema: dict,
    clave: str = "", reintentos: int = 3,
) -> dict:
    """Manda la petición y devuelve el JSON ya parseado y validado."""
    modelo = modelo_de(seccion)
    if not modelo:
        raise RuntimeError(
            "No hay modelo configurado. Con un proveedor que no sea Anthropic hay "
            "que poner DIGEST_MODEL (o DIGEST_MODEL_" + seccion.upper() + ")."
        )

    url, cabeceras, cuerpo = _peticion(modelo, system, usuario, esquema, clave or clave_api())
    datos = json.dumps(cuerpo).encode("utf-8")
    ultimo_error = ""

    cruda = None
    for intento in range(1, reintentos + 1):
        peticion = urllib.request.Request(url, data=datos, method="POST", headers=cabeceras)
        try:
            with urllib.request.urlopen(peticion, timeout=300) as respuesta:
                cruda = json.loads(respuesta.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            cuerpo_error = exc.read().decode("utf-8", "replace")[:500]
            ultimo_error = f"HTTP {exc.code}: {cuerpo_error}"
            # 4xx que no sea rate limit es un fallo nuestro: no insistimos.
            if exc.code != 429 and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            ultimo_error = str(exc)

        if intento < reintentos:
            espera = 2 ** intento
            log(f"  reintento {intento}/{reintentos - 1} en {espera}s ({ultimo_error})")
            time.sleep(espera)

    if cruda is None:
        raise RuntimeError(f"La API del modelo falló: {ultimo_error}")

    texto, uso = _leer(cruda)
    log(f"  tokens: {uso}")

    texto = texto.strip()
    if texto.startswith("```"):  # por si el modelo envuelve el JSON en un fence
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)

    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"La respuesta no es JSON válido: {texto[:500]}") from exc


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def trocear(bloques: list[str], limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Agrupa bloques enteros en mensajes sin partir un bloque por la mitad."""
    mensajes: list[str] = []
    actual = ""
    for bloque in bloques:
        if actual and len(actual) + 2 + len(bloque) > limite:
            mensajes.append(actual)
            actual = bloque
        else:
            actual = f"{actual}\n\n{bloque}" if actual else bloque
    if actual:
        mensajes.append(actual)
    return mensajes


def enviar_telegram(mensajes: list[str], token: str, chat_id: str) -> None:
    for i, mensaje in enumerate(mensajes, start=1):
        datos = json.dumps({
            "chat_id": chat_id,
            "text": mensaje,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")

        peticion = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=datos,
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=60) as respuesta:
                respuesta.read()
        except urllib.error.HTTPError as exc:
            cuerpo = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Telegram devolvió HTTP {exc.code}: {cuerpo}") from exc

        log(f"  mensaje {i}/{len(mensajes)} enviado ({len(mensaje)} caracteres)")
        if i < len(mensajes):
            time.sleep(1)  # el límite de Telegram es ~30 mensajes/segundo
