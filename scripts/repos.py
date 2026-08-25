"""Repos en tendencia, leídos de github.com/trending.

GitHub **no publica** ninguna interfaz de máquina para trending: ni RSS, ni API,
ni feed. La única fuente del dato es la página HTML, así que aquí sí se raspa.
(La regla de "nada de scraping" del README es sobre las fuentes de noticias, y
su motivo es que los medios sí publican RSS: elegir HTML pudiendo usar el feed
sería quedarse con la opción frágil. Aquí no hay alternativa que elegir.)

El número que se publica es **el que da GitHub** — "1,234 stars today" — y se
muestra tal cual. Este módulo no calcula ninguna velocidad ni reordena nada: se
respeta el orden en que GitHub lista los repos, que ya es su ranking de
tendencia.

Al ser HTML, esto se rompe si GitHub cambia el marcado. Está anclado en lo más
estable que tiene la página (el enlace a /stargazers, itemprop y el texto
"stars today") en vez de en clases CSS, y si el parseo no saca ni un repo se
levanta un error en vez de aparentar que fue un día tranquilo.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

from comun import MODELO, escapar, extraer_datos, limpiar_html, llamar_claude, log, registrar_uso

URL_TRENDING = "https://github.com/trending"

# daily es la señal más afilada; weekly da algo de contexto para repos que suben
# sostenidamente sin picos. Se deduplica y daily manda.
PERIODOS = [("daily", "hoy"), ("weekly", "esta semana")]

MAX_CANDIDATOS = 25
MAX_ELEGIDOS = 8

# Un navegador cualquiera: con el User-Agent por defecto de urllib, GitHub responde 403.
CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    # El texto "stars today" que buscamos es inglés: no dejamos que se localice.
    "Accept-Language": "en-US,en;q=0.9",
}

# Anclas semánticas, no clases CSS. El enlace a /stargazers da de una el nombre
# del repo y su total de estrellas.
#
# Se captura TODO el contenido del <a> y se limpia después, en vez de exigir que
# el número venga pegado al '>': GitHub mete dentro del enlace el icono
#   <svg class="octicon octicon-star"></svg>
# antes de la cifra, y una expresión que pidiera dígitos inmediatos falla en
# silencio dejando el total a 0. Pasó de verdad.
RE_STARGAZERS = re.compile(
    r'href="/([^"/]+)/([^"/]+)/stargazers"[^>]*>(.{0,400}?)</a>', re.S | re.I
)
RE_NOMBRE_H2 = re.compile(r"<h2[^>]*>.*?href=\"/([^\"/]+)/([^\"/]+)\"", re.S | re.I)
RE_GANADAS = re.compile(r"([\d.,]+)\s*stars?\s+(today|this week|this month)", re.I)
RE_LENGUAJE = re.compile(r'itemprop="programmingLanguage"[^>]*>\s*([^<]+?)\s*<', re.I)
# El \b es imprescindible: sin él "<p[^>]*>" matchea "<path ...>" del <svg>
# que GitHub mete en el título, y la descripción se come medio artículo.
RE_DESCRIPCION = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)


def _texto(fragmento: str) -> str:
    """Quita etiquetas y DESPUÉS decodifica entidades.

    El orden importa: al revés, un "&lt;script&gt;" se convertiría en etiqueta y
    el limpiador se llevaría el texto por delante. Con RSS esto no hacía falta
    porque feedparser ya entrega las entidades decodificadas.
    """
    return html.unescape(limpiar_html(fragmento))


def _entero(texto: str) -> int:
    """'1,234' -> 1234. GitHub separa millares con coma en inglés."""
    digitos = re.sub(r"[^\d]", "", texto or "")
    return int(digitos) if digitos else 0


def _formatear(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".", ",")
    return str(n)


# --------------------------------------------------------------------------
# 1. Descarga y parseo
# --------------------------------------------------------------------------

def _descargar(periodo: str) -> str:
    peticion = urllib.request.Request(
        f"{URL_TRENDING}?since={periodo}", headers=CABECERAS
    )
    with urllib.request.urlopen(peticion, timeout=60) as respuesta:
        return respuesta.read().decode("utf-8", "replace")


def parsear(html: str) -> tuple[list[dict], dict]:
    """Saca un repo por cada <article> de la página. Devuelve (repos, diagnostico)."""
    diag = {
        "articulos": 0, "sinNombre": 0, "sinGanadas": 0,
        "sinTotal": 0, "sinLenguaje": 0, "sinDescripcion": 0, "ok": 0,
    }
    encontrados: list[dict] = []

    # Cada fila de la lista es un <article>. Se parte por ahí en vez de intentar
    # una expresión que abarque toda la página.
    for trozo in re.split(r"<article\b", html, flags=re.I)[1:]:
        fin = re.search(r"</article>", trozo, re.I)
        if fin:
            trozo = trozo[: fin.start()]
        diag["articulos"] += 1

        estrellas_total = 0
        coincidencia = RE_STARGAZERS.search(trozo)
        if coincidencia:
            duenio, repo, interior = coincidencia.groups()
            estrellas_total = _entero(_texto(interior))
            if not estrellas_total:
                diag["sinTotal"] += 1
        else:
            diag["sinTotal"] += 1
            # El enlace de stargazers es la vía principal; el <h2> es el respaldo.
            coincidencia = RE_NOMBRE_H2.search(trozo)
            if not coincidencia:
                diag["sinNombre"] += 1
                continue
            duenio, repo = coincidencia.groups()

        nombre = f"{duenio}/{repo}"

        ganadas, periodo_texto = 0, ""
        coincidencia = RE_GANADAS.search(trozo)
        if coincidencia:
            ganadas = _entero(coincidencia.group(1))
            periodo_texto = coincidencia.group(2).lower()
        else:
            diag["sinGanadas"] += 1

        lenguaje = ""
        coincidencia = RE_LENGUAJE.search(trozo)
        if coincidencia:
            lenguaje = _texto(coincidencia.group(1))
        else:
            diag["sinLenguaje"] += 1

        descripcion = ""
        coincidencia = RE_DESCRIPCION.search(trozo)
        if coincidencia:
            descripcion = _texto(coincidencia.group(1))
        else:
            diag["sinDescripcion"] += 1

        diag["ok"] += 1
        encontrados.append({
            "nombre": nombre,
            "url": f"https://github.com/{nombre}",
            "descripcion": descripcion,
            "lenguaje": lenguaje,
            "estrellas": estrellas_total,
            "ganadas": ganadas,
            "periodo_en": periodo_texto,
        })

    return encontrados, diag


def obtener_candidatos() -> tuple[list[dict], dict]:
    """Lee las páginas de trending. Conserva el orden de GitHub: es su ranking."""
    por_nombre: dict[str, dict] = {}
    orden: list[str] = []
    diag = {"paginas": {}, "fallos": 0}

    for periodo, etiqueta in PERIODOS:
        try:
            html = _descargar(periodo)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            log(f"  AVISO no se pudo leer trending '{periodo}' -> {exc}")
            diag["fallos"] += 1
            continue

        encontrados, diag_pagina = parsear(html)
        diag["paginas"][periodo] = diag_pagina
        log(f"  {periodo}: {diag_pagina['ok']} repos de {diag_pagina['articulos']} filas")

        for repo in encontrados:
            if repo["nombre"] in por_nombre:
                continue  # daily va primero, así que gana daily
            repo["periodo"] = etiqueta
            por_nombre[repo["nombre"]] = repo
            orden.append(repo["nombre"])

    candidatos = [por_nombre[n] for n in orden][:MAX_CANDIDATOS]
    return candidatos, diag


# --------------------------------------------------------------------------
# 2. Explicación con Claude
# --------------------------------------------------------------------------

SYSTEM = "\n".join([
    "Eres un analista que prepara un briefing diario para un desarrollador de software.",
    "Recibes la lista de repositorios en tendencia de GitHub, en el orden en que GitHub los publica,",
    "con las estrellas que ha ganado cada uno y su descripción.",
    "",
    f"Tu tarea: elegir los {MAX_ELEGIDOS} MÁS relevantes y explicar cada uno.",
    "",
    "Criterios de relevancia, en este orden:",
    "1. Herramientas que un desarrollador podría usar de verdad en su trabajo.",
    "2. Proyectos que marcan una tendencia técnica real (nuevos enfoques, nuevas capas del stack).",
    "3. Alternativas libres a cosas de pago, o piezas de infraestructura importantes.",
    "Penaliza: listas de enlaces (awesome-*), material de estudio, colecciones de prompts,",
    "repos de cursos, clones sin nada nuevo y proyectos sin código propio.",
    "",
    "Reglas de salida:",
    "- Escribe SIEMPRE en español de España.",
    '- "nombre": cópialo EXACTAMENTE como aparece en el candidato (formato owner/repo).',
    '- "para_que_sirve": 1 o 2 frases explicando QUÉ HACE la herramienta y QUÉ PROBLEMA resuelve,',
    "  en lenguaje llano, como se lo explicarías a un colega. Nada de jerga de marketing,",
    "  nada de repetir la descripción del repo palabra por palabra. Si la descripción es vaga",
    "  o está en inglés técnico, tradúcela a algo concreto y comprensible.",
    '- "por_que_sube": 1 frase sobre por qué está llamando la atención ahora.',
    '- "categoria": 1 a 3 palabras (ej. "agentes de IA", "base de datos", "herramienta CLI").',
    "- Ordena el array de más a menos relevante.",
    "- Si hay menos candidatos con valor real, devuelve menos. No rellenes.",
    '- "resumen_global": una frase sobre hacia dónde apunta lo que está subiendo.',
    "",
    "NO inventes números de estrellas: no te los pedimos y se añaden después.",
])

ESQUEMA = {
    "type": "object",
    "properties": {
        "repos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "para_que_sirve": {"type": "string"},
                    "por_que_sube": {"type": "string"},
                    "categoria": {"type": "string"},
                },
                "required": ["nombre", "para_que_sirve", "por_que_sube", "categoria"],
                "additionalProperties": False,
            },
        },
        "resumen_global": {"type": "string"},
    },
    "required": ["repos", "resumen_global"],
    "additionalProperties": False,
}


def construir_body(candidatos: list[dict]) -> dict:
    listado = "\n\n".join(
        "\n".join(filter(None, [
            f"[{i + 1}] {c['nombre']}",
            f"    lenguaje: {c['lenguaje'] or 'n/d'} | estrellas totales: {c['estrellas'] or 'n/d'}"
            f" | ganadas {c['periodo']}: {c['ganadas'] or 'n/d'}",
            f"    descripcion: {c['descripcion']}" if c["descripcion"] else "    descripcion: (sin descripción)",
        ]))
        for i, c in enumerate(candidatos)
    )

    return {
        "model": MODELO,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [
            {"role": "user", "content": f"Repositorios en tendencia:\n\n{listado}"}
        ],
        "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}},
    }


# --------------------------------------------------------------------------
# 3. Mensaje
# --------------------------------------------------------------------------

def formatear_bloques(datos: dict, candidatos: list[dict], fecha_texto: str) -> list[str]:
    """Une el texto de Claude con las cifras de GitHub.

    Las cifras salen SIEMPRE de `candidatos`, nunca de la respuesta del modelo.
    """
    por_nombre = {c["nombre"]: c for c in candidatos}

    bloques = [
        f"<b>🚀 Repos en tendencia — {escapar(fecha_texto)}</b>\n\n"
        f"<i>{escapar(datos.get('resumen_global'))}</i>"
    ]

    i = 0
    for elegido in datos.get("repos") or []:
        real = por_nombre.get(elegido.get("nombre"))
        if not real:
            log(f"  AVISO el modelo devolvió '{elegido.get('nombre')}', que no era candidato: se descarta")
            continue

        i += 1
        cifras = []
        if real["ganadas"]:
            cifras.append(f"+{_formatear(real['ganadas'])} ⭐ {escapar(real['periodo'])}")
        if real["estrellas"]:
            cifras.append(f"{_formatear(real['estrellas'])} en total")
        if real["lenguaje"]:
            cifras.append(escapar(real["lenguaje"]))

        bloques.append("\n".join(filter(None, [
            f"<b>{i}. {escapar(real['nombre'])}</b> · {escapar(elegido.get('categoria'))}",
            " · ".join(cifras) if cifras else None,
            "",
            f"<b>Para qué sirve:</b> {escapar(elegido.get('para_que_sirve'))}",
            f"<b>Por qué sube:</b> {escapar(elegido.get('por_que_sube'))}",
            f"<a href=\"{escapar(real['url'])}\">Ver en GitHub</a>",
        ])))

    if i == 0:
        return [sin_repos(fecha_texto)]

    return bloques


def sin_repos(fecha_texto: str) -> str:
    return (
        f"<b>🚀 Repos en tendencia — {escapar(fecha_texto)}</b>\n\n"
        "No se encontraron repositorios destacables hoy."
    )


# --------------------------------------------------------------------------
# Orquestación de la sección
# --------------------------------------------------------------------------

def generar(fecha_texto: str, api_key: str, solo_candidatos: bool = False) -> list[str]:
    log("Leyendo github.com/trending...")
    candidatos, diag = obtener_candidatos()
    log(f"Diagnóstico trending: {json.dumps(diag)}")
    log(f"Candidatos: {len(candidatos)}")

    # Sin candidatos no es "un día tranquilo": trending siempre tiene 25 filas.
    # O no se pudo descargar, o cambió el marcado de la página.
    if not candidatos:
        if diag["fallos"] == len(PERIODOS):
            raise RuntimeError("No se pudo descargar github.com/trending en ningún periodo.")
        raise RuntimeError(
            "Se descargó github.com/trending pero no se pudo extraer ni un repo: "
            "seguramente cambió el marcado de la página. Revisá parsear() en scripts/repos.py."
        )

    if solo_candidatos:
        for i, c in enumerate(candidatos[:15], start=1):
            log(f"  [{i}] +{c['ganadas']} ⭐ {c['periodo']:<12} (total {c['estrellas']:>7}) "
                f"[{c['lenguaje'] or '?'}] {c['nombre']} — {c['descripcion'][:50]}")
        return []

    log(f"Explicando con {MODELO}...")
    respuesta = llamar_claude(construir_body(candidatos), api_key)
    registrar_uso(respuesta)
    return formatear_bloques(extraer_datos(respuesta), candidatos, fecha_texto)
