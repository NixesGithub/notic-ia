#!/usr/bin/env python3
"""Digest diario por Telegram: noticias de IA + repos de GitHub en tendencia.

La sección de noticias es el port de `workflows/noticias-ia.json` a un script
autónomo, para poder ejecutarlo desde GitHub Actions en vez de depender de la
instancia local de n8n (y por tanto de que el portátil esté encendido a la hora
del digest). Su lógica es la misma que la de los nodos Code del workflow:
mismas fuentes y pesos, misma deduplicación, misma fórmula de puntuación, mismo
prompt y mismo esquema de salida. Si cambiás una de las dos versiones, cambiá
la otra.

La sección de repos (scripts/repos.py) sólo existe aquí; no tiene equivalente
en n8n.

Variables de entorno:
  ANTHROPIC_API_KEY    obligatoria salvo en --dry-run
  TELEGRAM_BOT_TOKEN   obligatoria salvo en --dry-run
  TELEGRAM_CHAT_ID     obligatoria salvo en --dry-run
  DIGEST_TZ            zona horaria del digest (por defecto Europe/Madrid)
  DIGEST_HOUR          hora local a la que debe salir (por defecto 9)
  DIGEST_MODEL         modelo de Anthropic (por defecto claude-sonnet-5)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser

import repos
import resumen
from comun import (
    AGENTE,
    HORA_DIGEST,
    MODELO,
    ZONA,
    escapar,
    enviar_telegram,
    extraer_datos,
    fecha_en_espanol,
    limpiar_html,
    llamar_claude,
    log,
    normalizar,
    registrar_uso,
    trocear,
)

MAX_CANDIDATOS = 40

# "peso" = fiabilidad/relevancia editorial de la fuente (se usa para rankear).
PESOS = {
    "techcrunch.com": 3,
    "theverge.com": 3,
    "arstechnica.com": 3,
    "technologyreview.com": 3,
    "wired.com": 2,
    "venturebeat.com": 2,
    "news.ycombinator.com": 2,
    "news.google.com": 1,
}

NOMBRES = {
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "arstechnica.com": "Ars Technica",
    "technologyreview.com": "MIT Tech Review",
    "wired.com": "Wired",
    "venturebeat.com": "VentureBeat",
    "news.ycombinator.com": "Hacker News",
}

# Las búsquedas de "IA" en Google News se llenan de contenido bursátil sindicado
# ("¿Deberías comprar acciones de Nvidia?"). No es noticia de IA: lo penalizamos.
RUIDO = [
    "motley fool", "benzinga", "zacks", "investing.com", "seeking alpha",
    "simply wall st", "24/7 wall st", "insider monkey", "barchart",
    "the globe and mail", "nasdaq.com", "fool.com",
]

# Términos que suelen marcar una noticia relevante de IA.
CLAVES = [
    "openai", "anthropic", "claude", "chatgpt", "gpt", "gemini", "deepmind",
    "llama", "mistral", "nvidia", "microsoft", "meta", "apple", "amazon",
    "model", "modelo", "agent", "agente", "launch", "lanza", "release",
    "funding", "raise", "ronda", "valuation", "acquisition", "adquiere",
    "regulation", "regula", "ley", "lawsuit", "demanda", "benchmark",
    "open source", "código abierto", "chip", "data center", "centro de datos",
]


# --------------------------------------------------------------------------
# 1. Fuentes
# --------------------------------------------------------------------------

def url_google_news(consulta: str, idioma: str, pais: str, desde: str, hasta: str) -> str:
    import urllib.parse
    q = urllib.parse.quote(f"{consulta} after:{desde} before:{hasta}")
    return (
        f"https://news.google.com/rss/search?q={q}"
        f"&hl={idioma}&gl={pais}&ceid={pais}:{idioma.split('-')[0]}"
    )


def construir_fuentes(referencia: datetime) -> list[dict]:
    """Genera las 9 URLs de RSS.

    Google News reordena por actualidad: sin acotar fechas devuelve casi sólo el
    día en curso. Con after:/before: alrededor del día de referencia sí lo cubre.
    """
    desde = (referencia - timedelta(days=1)).date().isoformat()
    hasta = (referencia + timedelta(days=1)).date().isoformat()

    return [
        {"fuente": "TechCrunch", "peso": 3, "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"fuente": "The Verge", "peso": 3, "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
        {"fuente": "Ars Technica", "peso": 3, "url": "https://arstechnica.com/ai/feed/"},
        {"fuente": "MIT Tech Review", "peso": 3, "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
        {"fuente": "Wired", "peso": 2, "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
        {"fuente": "VentureBeat", "peso": 2, "url": "https://venturebeat.com/category/ai/feed/"},
        {"fuente": "Hacker News", "peso": 2, "url": "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+OpenAI+OR+Anthropic"},
        {"fuente": "Google News (EN)", "peso": 1, "url": url_google_news("artificial intelligence", "en-US", "US", desde, hasta)},
        {"fuente": "Google News (ES)", "peso": 1, "url": url_google_news("inteligencia artificial", "es", "ES", desde, hasta)},
    ]


# --------------------------------------------------------------------------
# 2. Lectura de feeds
# --------------------------------------------------------------------------

def leer_feeds(fuentes: list[dict]) -> list[dict]:
    """Lee los feeds uno a uno. Un feed caído no puede tumbar el digest entero."""
    entradas: list[dict] = []
    fallos: list[str] = []

    for fuente in fuentes:
        try:
            feed = feedparser.parse(fuente["url"], agent=AGENTE)
        except Exception as exc:  # noqa: BLE001 - queremos seguir con el resto
            fallos.append(f"{fuente['fuente']}: {exc}")
            continue

        if getattr(feed, "bozo", 0) and not feed.entries:
            fallos.append(f"{fuente['fuente']}: {getattr(feed, 'bozo_exception', 'feed ilegible')}")
            continue

        log(f"  {fuente['fuente']}: {len(feed.entries)} entradas")
        entradas.extend(feed.entries)

    if fallos:
        for fallo in fallos:
            log(f"  AVISO fuente no leída -> {fallo}")
    if not entradas:
        raise RuntimeError("Ninguna fuente devolvió entradas: no hay nada que resumir.")

    return entradas


def fecha_de_entrada(entrada) -> datetime | None:
    """feedparser normaliza published/updated a UTC en *_parsed."""
    for campo in ("published_parsed", "updated_parsed"):
        valor = entrada.get(campo)
        if valor:
            try:
                return datetime(*valor[:6], tzinfo=timezone.utc).astimezone(ZONA)
            except (TypeError, ValueError):
                continue
    return None


# --------------------------------------------------------------------------
# 3. Filtrado, deduplicación y ranking
# --------------------------------------------------------------------------

def seleccionar(entradas: list[dict], desde: datetime, hasta: datetime) -> tuple[list[dict], dict]:
    """Filtra por ventana temporal, deduplica entre medios y rankea."""
    por_clave: dict[str, dict] = {}
    diag = {
        "total": 0, "sinLink": 0, "sinFecha": 0,
        "fueraDeRango": 0, "sinHost": 0, "ok": 0,
    }

    for entrada in entradas:
        diag["total"] += 1

        link = entrada.get("link") or entrada.get("id") or ""
        if not link:
            diag["sinLink"] += 1
            continue

        fecha = fecha_de_entrada(entrada)
        if fecha is None:
            diag["sinFecha"] += 1
            continue
        if fecha < desde or fecha > hasta:
            diag["fueraDeRango"] += 1
            continue

        match = re.match(r"^https?://([^/?#]+)", str(link), re.I)
        if not match:
            diag["sinHost"] += 1
            continue
        host = re.sub(r":\d+$", "", match.group(1).lower().removeprefix("www."))
        diag["ok"] += 1

        titulo = limpiar_html(entrada.get("title"))
        fuente = NOMBRES.get(host, host)

        # Google News agrega " - Medio" al final del título; lo separamos.
        if host == "news.google.com":
            partes = titulo.split(" - ")
            if len(partes) > 1:
                fuente = partes.pop().strip()
                titulo = " - ".join(partes).strip()
            else:
                fuente = "Google News"

        clave = " ".join(normalizar(titulo).split(" ")[:9])
        if not clave:
            continue

        extracto = limpiar_html(entrada.get("summary") or entrada.get("description"))
        texto = normalizar(f"{titulo} {extracto}")
        aciertos = sum(1 for k in CLAVES if normalizar(k) in texto)
        peso = PESOS.get(host, 1)

        existente = por_clave.get(clave)
        if existente:
            # La misma noticia en varias fuentes es señal fuerte de importancia.
            existente["apariciones"] += 1
            if peso > existente["peso"]:
                existente.update(peso=peso, titulo=titulo, url=link, fuente=fuente)
            continue

        por_clave[clave] = {
            "titulo": titulo,
            "url": link,
            "fuente": fuente,
            "hora": fecha.strftime("%H:%M"),
            "extracto": extracto[:300],
            "peso": peso,
            "aciertos": aciertos,
            "apariciones": 1,
        }

    candidatos = []
    for c in por_clave.values():
        es_ruido = any(r in c["fuente"].lower() for r in RUIDO)
        c["score"] = c["peso"] * 2 + c["aciertos"] + c["apariciones"] * 2 - (12 if es_ruido else 0)
        if c["score"] > 0:
            candidatos.append(c)

    candidatos.sort(key=lambda c: c["score"], reverse=True)
    return candidatos[:MAX_CANDIDATOS], diag


# --------------------------------------------------------------------------
# 4. Resumen con Claude
# --------------------------------------------------------------------------

SYSTEM = "\n".join([
    "Eres un analista que prepara un briefing diario de noticias de inteligencia artificial para un desarrollador de software.",
    "Recibes una lista de titulares candidatos del día anterior, ya deduplicados.",
    "",
    "Tu tarea: elegir las 10 noticias MÁS IMPORTANTES y resumir cada una.",
    "",
    "Criterios de importancia, en este orden:",
    "1. Impacto real: lanzamientos de modelos, cambios de capacidad, precios, disponibilidad de APIs.",
    "2. Movimientos estructurales del sector: financiación relevante, adquisiciones, regulación, litigios.",
    "3. Avances técnicos o de investigación con consecuencias prácticas.",
    "Penaliza: opinión, listicles, contenido promocional, refritos y notas sin hecho nuevo.",
    "",
    "Reglas de salida:",
    "- Escribe SIEMPRE en español de España.",
    '- "titulo": máximo 80 caracteres, sin comillas ni emojis.',
    '- "resumen": 1 o 2 frases con el hecho concreto y por qué importa. Nada de relleno.',
    '- "url" y "fuente": cópialos EXACTAMENTE del candidato elegido. No inventes URLs.',
    '- "importancia": 1 (menor) a 10 (mayor).',
    "- Ordena el array de mayor a menor importancia.",
    "- Si hay menos de 10 candidatos con valor real, devuelve menos. No rellenes.",
    '- "resumen_global": una frase sobre el tema dominante del día.',
])

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulares": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "resumen": {"type": "string"},
                    "fuente": {"type": "string"},
                    "url": {"type": "string"},
                    "importancia": {"type": "integer", "enum": list(range(1, 11))},
                },
                "required": ["titulo", "resumen", "fuente", "url", "importancia"],
                "additionalProperties": False,
            },
        },
        "resumen_global": {"type": "string"},
    },
    "required": ["titulares", "resumen_global"],
    "additionalProperties": False,
}


def construir_body(candidatos: list[dict], fecha_texto: str) -> dict:
    listado = "\n\n".join(
        "\n".join(filter(None, [
            f"[{i + 1}] {c['titulo']}",
            f"    fuente: {c['fuente']} | hora: {c['hora']} | apariciones: {c['apariciones']}",
            f"    url: {c['url']}",
            f"    extracto: {c['extracto']}" if c["extracto"] else None,
        ]))
        for i, c in enumerate(candidatos)
    )

    return {
        # Sonnet 5 en vez de Opus: resumir titulares no necesita el modelo más caro.
        "model": MODELO,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [
            {"role": "user", "content": f"Titulares candidatos del {fecha_texto}:\n\n{listado}"}
        ],
        "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}},
    }


# --------------------------------------------------------------------------
# 5. Mensaje
# --------------------------------------------------------------------------

def formatear_bloques(datos: dict, fecha_texto: str) -> list[str]:
    bloques = [
        f"<b>🤖 Noticias IA — {escapar(fecha_texto)}</b>\n\n"
        f"<i>{escapar(datos.get('resumen_global'))}</i>"
    ]

    for i, n in enumerate(datos.get("titulares") or [], start=1):
        bloques.append("\n".join([
            f"<b>{i}. {escapar(n.get('titulo'))}</b>",
            escapar(n.get("resumen")),
            f"<a href=\"{escapar(n.get('url'))}\">{escapar(n.get('fuente'))}</a>"
            f" · importancia {escapar(n.get('importancia'))}/10",
        ]))

    return bloques


def sin_noticias(fecha_texto: str) -> list[str]:
    return [
        f"<b>🤖 Noticias IA — {escapar(fecha_texto)}</b>\n\n"
        "No se encontraron noticias de IA publicadas ese día en las fuentes configuradas."
    ]


def generar_noticias(
    desde: datetime, hasta: datetime, referencia: datetime,
    fecha_texto: str, api_key: str, solo_candidatos: bool = False,
) -> tuple[list[str], list[dict]]:
    """Devuelve (bloques de Telegram, candidatos crudos).

    Los candidatos salen también hacia fuera porque la sección de resumen los
    lee SIN filtrar: el top 10 de aquí está rankeado con otro criterio.
    """
    log("Leyendo fuentes RSS...")
    entradas = leer_feeds(construir_fuentes(referencia))

    candidatos, diag = seleccionar(entradas, desde, hasta)
    # diagnóstico explícito: un filtrado que colapsa a 0 parece "un día tranquilo".
    log(f"Filtrado noticias: {json.dumps(diag)}")
    log(f"Candidatos seleccionados: {len(candidatos)}")

    if solo_candidatos:
        for i, c in enumerate(candidatos[:15], start=1):
            log(f"  [{i}] ({c['score']}) {c['fuente']} — {c['titulo']}")
        return [], candidatos

    if not candidatos:
        return sin_noticias(fecha_texto), []

    log(f"Resumiendo con {MODELO}...")
    respuesta = llamar_claude(construir_body(candidatos, fecha_texto), api_key)
    registrar_uso(respuesta)
    return formatear_bloques(extraer_datos(respuesta), fecha_texto), candidatos


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------

def ventana(modo: str, ahora: datetime) -> tuple[datetime, datetime, datetime, str]:
    """Devuelve (desde, hasta, referencia, texto) según el modo de ventana."""
    if modo == "ultimas24h":
        desde = ahora - timedelta(hours=24)
        return desde, ahora, ahora, f"últimas 24 h hasta {ahora.strftime('%d/%m %H:%M')}"

    ayer = ahora - timedelta(days=1)
    desde = ayer.replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = ayer.replace(hour=23, minute=59, second=59, microsecond=999999)
    return desde, hasta, ayer, fecha_en_espanol(ayer)


def main() -> int:
    parser = argparse.ArgumentParser(description="Digest diario de IA por Telegram.")
    parser.add_argument(
        "--ventana", choices=["ayer", "ultimas24h"], default="ayer",
        help="'ayer' (día natural anterior, el digest de las 9) o 'ultimas24h' (para probar a cualquier hora). Sólo afecta a las noticias.",
    )
    parser.add_argument(
        "--secciones", default="noticias,repos,resumen",
        help="Qué enviar, separado por comas: noticias, repos, resumen. Por defecto las tres. "
             "'resumen' se envía siempre el último y necesita al menos una de las otras dos.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Salta la comprobación de que sean las %d:00 en la zona configurada." % HORA_DIGEST,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No llama a Anthropic ni a Telegram: imprime los candidatos seleccionados.",
    )
    parser.add_argument(
        "--marca", metavar="ARCHIVO",
        help="Crea este archivo sólo si el digest llegó a enviarse. Lo usa el "
             "workflow para no repetir el envío si el cron se retrasa.",
    )
    args = parser.parse_args()

    VALIDAS = ("noticias", "repos", "resumen")
    secciones = [s.strip() for s in args.secciones.split(",") if s.strip()]
    desconocidas = [s for s in secciones if s not in VALIDAS]
    if desconocidas or not secciones:
        log(f"ERROR: secciones no válidas: {desconocidas or '(ninguna)'}. Usá: {', '.join(VALIDAS)}.")
        return 1
    if secciones == ["resumen"]:
        log("ERROR: 'resumen' filtra el material de las otras secciones; no puede ir solo.")
        return 1
    # El resumen lee lo que recogen las otras dos, así que siempre va el último.
    secciones = [s for s in VALIDAS if s in secciones]

    def marcar_enviado() -> None:
        if not args.marca:
            return
        ruta = pathlib.Path(args.marca)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(f"{datetime.now(ZONA).isoformat()}\n", encoding="utf-8")

    ahora = datetime.now(ZONA)

    # El cron de GitHub Actions sólo entiende UTC, así que el workflow dispara a
    # dos horas UTC distintas y es aquí donde se decide cuál corresponde a la
    # hora local pedida. Así el digest sale a la misma hora todo el año, con y
    # sin horario de verano.
    if not args.force and ahora.hour != HORA_DIGEST:
        log(
            f"Son las {ahora:%H:%M} en {ZONA.key} y el digest es a las {HORA_DIGEST}:00. "
            "No toca: salgo sin hacer nada."
        )
        return 0

    desde, hasta, referencia, fecha_texto = ventana(args.ventana, ahora)
    log(f"Digest de: {fecha_texto} ({ZONA.key}) | secciones: {', '.join(secciones)}")

    if not args.dry_run:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        faltan = [
            nombre for nombre, valor in [
                ("ANTHROPIC_API_KEY", api_key),
                ("TELEGRAM_BOT_TOKEN", token),
                ("TELEGRAM_CHAT_ID", chat_id),
            ] if not valor
        ]
        if faltan:
            log(f"ERROR: faltan variables de entorno: {', '.join(faltan)}")
            return 1
    else:
        token = chat_id = api_key = ""

    # Cada sección es independiente: que falle la de repos no puede dejarte sin
    # noticias, ni al revés.
    enviadas, fallidas = 0, []
    material = {"noticias": [], "repos": []}
    for seccion in secciones:
        log(f"--- {seccion} ---")
        try:
            if seccion == "noticias":
                bloques, material["noticias"] = generar_noticias(
                    desde, hasta, referencia, fecha_texto, api_key, args.dry_run
                )
            elif seccion == "repos":
                bloques, material["repos"] = repos.generar(
                    fecha_texto, api_key, args.dry_run
                )
            else:
                bloques = resumen.generar(
                    material["noticias"], material["repos"],
                    fecha_texto, api_key, args.dry_run,
                )

            if args.dry_run or not bloques:
                continue

            mensajes = trocear(bloques)
            log(f"Enviando {len(mensajes)} mensaje(s) a Telegram...")
            enviar_telegram(mensajes, token, chat_id)
            enviadas += 1
        except (RuntimeError, OSError) as exc:
            log(f"ERROR en la sección '{seccion}': {exc}")
            fallidas.append(seccion)

    if args.dry_run:
        log("--dry-run: no se llamó a Anthropic ni a Telegram.")
        return 1 if fallidas else 0

    if enviadas:
        marcar_enviado()

    if fallidas:
        log(f"Terminado con fallos en: {', '.join(fallidas)}")
        return 1

    log("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
