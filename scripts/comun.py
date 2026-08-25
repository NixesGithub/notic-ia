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

ZONA = ZoneInfo(os.environ.get("DIGEST_TZ", "Europe/Madrid"))
HORA_DIGEST = int(os.environ.get("DIGEST_HOUR", "9"))
MODELO = os.environ.get("DIGEST_MODEL", "claude-sonnet-5")

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


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

def llamar_claude(body: dict, api_key: str, reintentos: int = 3) -> dict:
    datos = json.dumps(body).encode("utf-8")
    ultimo_error = ""

    for intento in range(1, reintentos + 1):
        peticion = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=datos,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                # La credencial sólo inyecta x-api-key: la versión va aparte.
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(peticion, timeout=300) as respuesta:
                return json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            cuerpo = exc.read().decode("utf-8", "replace")[:500]
            ultimo_error = f"HTTP {exc.code}: {cuerpo}"
            # 4xx que no sea rate limit es un fallo nuestro: no insistimos.
            if exc.code != 429 and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            ultimo_error = str(exc)

        if intento < reintentos:
            espera = 2 ** intento
            log(f"  reintento {intento}/{reintentos - 1} en {espera}s ({ultimo_error})")
            time.sleep(espera)

    raise RuntimeError(f"La API de Anthropic falló: {ultimo_error}")


def extraer_datos(respuesta: dict) -> dict:
    """Saca el JSON del bloque de texto, comprobando antes los rechazos."""
    if respuesta.get("stop_reason") == "refusal":
        detalle = (respuesta.get("stop_details") or {}).get("explanation", "sin detalle")
        raise RuntimeError(f"Anthropic rechazó la petición: {detalle}")

    bloque = next((b for b in respuesta.get("content", []) if b.get("type") == "text"), None)
    if not bloque:
        raise RuntimeError(
            "La respuesta no contiene ningún bloque de texto: "
            + json.dumps(respuesta)[:500]
        )

    texto = bloque["text"].strip()
    if texto.startswith("```"):  # por si el modelo envuelve el JSON en un fence
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)

    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"La respuesta no es JSON válido: {texto[:500]}") from exc


def registrar_uso(respuesta: dict) -> None:
    uso = respuesta.get("usage", {})
    log(
        f"  tokens: entrada {uso.get('input_tokens', '?')} / "
        f"salida {uso.get('output_tokens', '?')} | modelo {respuesta.get('model')}"
    )


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
