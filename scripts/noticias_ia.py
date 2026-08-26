#!/usr/bin/env python3
"""Digest diario por Telegram: noticias de IA + repos de GitHub en tendencia.

La sección de noticias es el port de `workflows/noticias-ia.json` a un script
autónomo, para poder ejecutarlo desde GitHub Actions en vez de depender de la
instancia local de n8n (y por tanto de que el portátil esté encendido a la hora
del digest). Su lógica es la misma que la de los nodos Code del workflow:
mismas fuentes y pesos, misma deduplicación, misma fórmula de puntuación, mismo
prompt y mismo esquema de salida. Si cambiás una de las dos versiones, cambiá
la otra.

Las secciones de repos, resumen y laboratorio sólo existen aquí; no tienen
equivalente en n8n.

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

import laboratorio
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

MAX_CANDIDATOS = 50

# (suelo, techo) por temática acotada. El suelo existe porque sin él nunca
# entran: puntúan con palabras clave de IA que no tienen. El techo existe
# porque con las palabras clave propias se volvieron demasiado competitivas y
# una fuente prolífica —Adafruit publica a diario— se comía 5 de los 50 huecos.
#
# Música va deliberadamente por debajo: interesa, pero mucho menos que la
# tecnología, los inventos y el trabajo de programador. Con techo 4 sobre 50
# candidatos no puede pasar del 8% del digest, y con suelo 1 puede casi
# desaparecer los días sin nada bueno. Fabricación alimenta la faceta de
# inventor, así que conserva el suelo de 2.
CUPOS = {"musica": (1, 4), "fabricacion": (2, 4)}

# Suelo de peso por host. Desde que cada fuente declara el suyo en
# construir_fuentes(), esto es sólo una red de seguridad: si un feed se añade
# sin peso, su host todavía puede rescatarlo. El peso real es el máximo de
# ambos. Los nombres de abajo sólo se usan en las barridas genéricas.
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
    # Ojo: "funding", "raise", "ronda", "valuation" y "acquisition" estaban aquí,
    # heredadas del diseño original. Se quitaron a propósito: premiaban las
    # noticias de dinero, que son las que el perfil descarta explícitamente, y
    # les hacían ganar huecos del corte a cosas que el resumen sí aprovecha.
    "regulation", "regula", "ley", "lawsuit", "demanda", "benchmark",
    "open source", "código abierto", "chip", "data center", "centro de datos",
    # Agentes que operan solos: capacidad nueva, no producto. Está en el perfil
    # como caso relevante, así que también tiene que puntuar en el ranking.
    "autonomous", "autónomo", "computer use", "browser agent", "mcp",
    "claude code", "codex", "cli", "sdk", "api",
    # Música: sin estas, una fuente musical entra por cupo pero puntúa a cero.
    "music", "música", "audio", "plugin", "vst", "daw", "synth", "sinte",
    "stem", "pista", "mastering", "masterización", "voz", "sample",
    # Fabricación y prototipado, para la faceta de inventor.
    "3d print", "impresión 3d", "raspberry", "arduino", "esp32", "robot",
    "cad", "prototip", "sensor", "firmware", "maker",
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


# Repos cuyas *releases* se siguen. GitHub publica un Atom por repo sin API ni
# credenciales: es la señal más directa que hay de "la herramienta que uso sacó
# versión". Añadí los tuyos aquí; un repo mal escrito sale como AVISO en el log
# y no rompe nada.
HERRAMIENTAS_SEGUIDAS = [
    "anthropics/claude-code",
    "ollama/ollama",
    "n8n-io/n8n",
]


def construir_fuentes(referencia: datetime) -> list[dict]:
    """Genera las URLs de todos los feeds.

    Cada fuente declara su propio `peso`, y ese peso viaja con cada entrada hasta
    el ranking (ver `leer_feeds`). Antes el peso se deducía del host del enlace,
    lo que hundía a cualquier fuente servida por un intermediario: una entrada de
    Anthropic vía Google News aterrizaba en `news.google.com` y cobraba peso 1,
    el más bajo, por muy primaria que fuese la fuente.

    `tematica` marca las que no son de IA para poder reservarles cupo: sin eso
    quedan siempre fuera del corte, porque puntúan con palabras clave de IA que
    no tienen.

    Google News reordena por actualidad: sin acotar fechas devuelve casi sólo el
    día en curso. Con after:/before: alrededor del día de referencia sí lo cubre.
    """
    desde = (referencia - timedelta(days=1)).date().isoformat()
    hasta = (referencia + timedelta(days=1)).date().isoformat()

    def gnews(consulta: str, idioma: str = "en-US", pais: str = "US") -> str:
        return url_google_news(consulta, idioma, pais, desde, hasta)

    return [
        # --- Releases de las herramientas que usás. La señal más directa. ---
        *[
            {"fuente": f"release: {repo}", "peso": 5,
             "url": f"https://github.com/{repo}/releases.atom"}
            for repo in HERRAMIENTAS_SEGUIDAS
        ],

        # --- Fuentes primarias: el anuncio, no la crónica del anuncio. ---
        {"fuente": "OpenAI", "peso": 4, "url": "https://openai.com/news/rss.xml"},
        {"fuente": "Hugging Face", "peso": 4, "url": "https://huggingface.co/blog/feed.xml"},
        {"fuente": "Google DeepMind", "peso": 4, "url": "https://deepmind.google/blog/rss.xml"},
        {"fuente": "Google Research", "peso": 4, "url": "https://research.google/blog/rss/"},
        {"fuente": "Ollama", "peso": 4, "url": "https://ollama.com/blog/rss.xml"},
        # Anthropic, Meta y Mistral NO publican RSS (probados y confirmados 404).
        # Google News acotado por dominio es el único acceso, y es de segunda
        # mano: puede llegar tarde o incompleto. El peso 4 lo compensa.
        {"fuente": "Anthropic", "peso": 4, "url": gnews("site:anthropic.com")},
        {"fuente": "Meta AI", "peso": 4, "url": gnews("site:ai.meta.com")},
        {"fuente": "Mistral", "peso": 4, "url": gnews("site:mistral.ai")},

        # --- Prensa. ---
        {"fuente": "TechCrunch", "peso": 3, "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"fuente": "The Verge", "peso": 3, "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
        {"fuente": "Ars Technica", "peso": 3, "url": "https://arstechnica.com/ai/feed/"},
        {"fuente": "MIT Tech Review", "peso": 3, "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
        {"fuente": "Wired", "peso": 2, "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
        {"fuente": "VentureBeat", "peso": 2, "url": "https://venturebeat.com/category/ai/feed/"},
        {"fuente": "InfoQ", "peso": 2, "url": "https://feed.infoq.com/ai-ml-data-eng/"},

        # --- Comunidad: detecta herramientas antes que la prensa. ---
        {"fuente": "Simon Willison", "peso": 3, "url": "https://simonwillison.net/atom/everything/"},
        {"fuente": "Show HN", "peso": 2, "url": "https://hnrss.org/show"},
        {"fuente": "Hacker News", "peso": 2, "url": "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+OpenAI+OR+Anthropic"},
        {"fuente": "Lobsters", "peso": 2, "url": "https://lobste.rs/t/ai.rss"},
        {"fuente": "r/LocalLLaMA", "peso": 2, "url": "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day"},

        # --- Divulgación en español. Comentario, no primicia: peso medio. ---
        {"fuente": "midudev", "peso": 2,
         "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC3aj05GEEyzdOqYM5FLSFeg"},
        {"fuente": "Dot CSV", "peso": 2,
         "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCOTko-zmnQTcOxSRdg5_uOQ"},
        {"fuente": "Dot CSV Lab", "peso": 2,
         "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCy5znSnfMsDwaLlROnZ7Qbg"},
        {"fuente": "Gentleman Programming", "peso": 2,
         "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbx_d228PdYwgB4Jz202SIQ"},
        # Sólo YouTube: lo que estos divulgadores publican en X o LinkedIn no es
        # accesible. X cerró su API gratuita y las instancias de Nitter están
        # caídas (nitter.net responde 410); LinkedIn no tiene RSS, redirige a
        # login y sus términos prohíben el scraping. Un puente no oficial se
        # rompería en silencio, que es justo lo que este repo evita.

        # --- Regulación: la excepción del perfil sobre disputas con laboratorios. ---
        {"fuente": "Regulación IA", "generica": True, "peso": 2,
         "url": gnews("AI regulation OR lawsuit OpenAI OR Anthropic OR Google")},

        # --- Música. Tienen cupo propio: con palabras clave de IA nunca llegarían. ---
        {"fuente": "CDM", "peso": 2, "tematica": "musica", "url": "https://cdm.link/feed/"},
        {"fuente": "Bedroom Producers", "peso": 2, "tematica": "musica",
         "url": "https://bedroomproducersblog.com/feed/"},
        {"fuente": "MusicTech", "peso": 2, "tematica": "musica", "url": "https://musictech.com/feed/"},

        # --- Fabricación y prototipado, para la faceta de inventor. ---
        {"fuente": "Hackaday", "peso": 2, "tematica": "fabricacion", "url": "https://hackaday.com/feed/"},
        {"fuente": "Hackster", "peso": 2, "tematica": "fabricacion", "url": "https://www.hackster.io/news.atom"},
        {"fuente": "Adafruit", "peso": 2, "tematica": "fabricacion", "url": "https://blog.adafruit.com/feed/"},

        # --- Barrido general, al final y con el peso más bajo. ---
        {"fuente": "Google News (EN)", "generica": True, "peso": 1, "url": gnews("artificial intelligence")},
        {"fuente": "Google News (ES)", "generica": True, "peso": 1, "url": gnews("inteligencia artificial", "es", "ES")},
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
        # El peso viaja con la entrada. Deducirlo del host del enlace hundía a
        # cualquier fuente servida por un intermediario (Anthropic vía Google
        # News acababa valiendo 1, como el barrido genérico).
        entradas.extend({
            "entrada": e,
            "peso": fuente["peso"],
            "fuente": fuente["fuente"],
            "tematica": fuente.get("tematica", "ia"),
            "generica": fuente.get("generica", False),
        } for e in feed.entries)

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
        "fueraDeRango": 0, "sinHost": 0, "tituloCorto": 0, "ok": 0,
    }

    for item in entradas:
        diag["total"] += 1
        entrada = item["entrada"]

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
        # El nombre declarado por el feed manda: es más preciso que adivinarlo
        # por el host. Sólo las barridas genéricas necesitan que se deduzca.
        fuente = item["fuente"] if not item["generica"] else NOMBRES.get(host, host)

        # Google News agrega " - Medio" al final del título. En las barridas eso
        # identifica al medio real; en las acotadas por dominio ya lo sabemos.
        if host == "news.google.com":
            partes = titulo.split(" - ")
            if len(partes) > 1:
                if item["generica"]:
                    fuente = partes.pop().strip()
                else:
                    partes.pop()
                titulo = " - ".join(partes).strip()
            elif item["generica"]:
                fuente = "Google News"

        palabras = normalizar(titulo).split(" ")
        clave = " ".join(palabras[:9])
        if not clave:
            continue
        # Acotar un feed por dominio arrastra páginas que no son noticias
        # ("Jobs", "Careers", "Pricing"). Un titular de verdad no baja de tres
        # palabras, así que ese es el corte más barato y menos arbitrario.
        if len(palabras) < 3:
            diag["tituloCorto"] += 1
            continue

        extracto = limpiar_html(entrada.get("summary") or entrada.get("description"))
        texto = normalizar(f"{titulo} {extracto}")
        aciertos = sum(1 for k in CLAVES if normalizar(k) in texto)
        # El peso del feed es un suelo, no un sustituto: en las barridas el host
        # sigue mandando, porque ahí cada entrada viene de un medio distinto.
        peso = max(PESOS.get(host, 1), item["peso"])

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
            "tematica": item["tematica"],
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
    return aplicar_cupos(candidatos, MAX_CANDIDATOS, CUPOS), diag


def aplicar_cupos(
    ordenados: list[dict], maximo: int, cupos: dict[str, tuple[int, int]]
) -> list[dict]:
    """Corta en `maximo` respetando un suelo y un techo por temática acotada.

    Las noticias de música o electrónica no llevan las palabras clave de IA, así
    que por ranking puro quedarían siempre fuera y esas fuentes serían
    decorativas: de ahí el suelo. Pero con sus propias palabras clave pasaron a
    competir de más, y una fuente que publica a diario acaparaba el corte: de ahí
    el techo. El hueco se le quita o se le da siempre a la peor de IA, nunca a
    otra temática acotada.
    """
    seleccion = list(ordenados[:maximo])
    pendientes = list(ordenados[maximo:])

    def cuantas(tema: str) -> int:
        return sum(1 for c in seleccion if c["tematica"] == tema)

    def sacar_peor(tema: str) -> dict | None:
        for k in range(len(seleccion) - 1, -1, -1):
            if seleccion[k]["tematica"] == tema:
                return seleccion.pop(k)
        return None

    def coger_mejor(tema: str) -> dict | None:
        for k, c in enumerate(pendientes):
            if c["tematica"] == tema:
                return pendientes.pop(k)
        return None

    # Techo primero: libera huecos que el suelo de otra temática podría usar.
    for tema, (_, techo) in cupos.items():
        while cuantas(tema) > techo:
            sacar_peor(tema)
            relleno = coger_mejor("ia")
            if relleno is not None:
                seleccion.append(relleno)

    for tema, (suelo, _) in cupos.items():
        while cuantas(tema) < suelo:
            entrante = coger_mejor(tema)
            if entrante is None:
                break  # no hay candidatos de esa temática
            if len(seleccion) >= maximo and sacar_peor("ia") is None:
                break  # no queda nada de IA que sacrificar
            seleccion.append(entrante)

    seleccion.sort(key=lambda c: c["score"], reverse=True)
    return seleccion


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
        "--secciones", default="noticias,repos,resumen,laboratorio",
        help="Qué enviar, separado por comas: noticias, repos, resumen, laboratorio. Por defecto "
             "las cuatro. 'resumen' y 'laboratorio' filtran el material que recogen las otras "
             "dos, así que van al final y no pueden ir solas.",
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

    VALIDAS = ("noticias", "repos", "resumen", "laboratorio")
    # Estas dos no recogen material propio: filtran el de las otras.
    DERIVADAS = ("resumen", "laboratorio")

    secciones = [s.strip() for s in args.secciones.split(",") if s.strip()]
    desconocidas = [s for s in secciones if s not in VALIDAS]
    if desconocidas or not secciones:
        log(f"ERROR: secciones no válidas: {desconocidas or '(ninguna)'}. Usá: {', '.join(VALIDAS)}.")
        return 1
    if all(s in DERIVADAS for s in secciones):
        log(f"ERROR: {' y '.join(DERIVADAS)} filtran el material de las otras secciones; "
            "hace falta pedir 'noticias' o 'repos' también.")
        return 1
    # Las derivadas leen lo que recogen las otras, así que van siempre al final.
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
            elif seccion == "resumen":
                bloques = resumen.generar(
                    material["noticias"], material["repos"],
                    fecha_texto, api_key, args.dry_run,
                )
            else:
                # Puede devolver [] a propósito: sin cruces no se manda nada.
                bloques = laboratorio.generar(
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
