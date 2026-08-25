"""Repositorios de GitHub que están ganando estrellas rápido, explicados.

GitHub no publica una API de "trending": la página github.com/trending es HTML,
y este proyecto no raspa HTML a propósito. Se usa la API de búsqueda oficial,
que sí es estable, y la velocidad se calcula aquí:

    estrellas_dia = estrellas / días desde la creación

Es un promedio desde el día cero, no las estrellas ganadas esta semana — la API
no expone el histórico de estrellas. Para repos jóvenes, que son los que buscamos,
el promedio y la velocidad actual se parecen bastante; un repo de 2015 con 50.000
estrellas queda con un promedio bajo y no aparece, que es justo lo que queremos.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from comun import (
    AGENTE,
    MODELO,
    ZONA,
    escapar,
    extraer_datos,
    llamar_claude,
    log,
    registrar_uso,
)

# Dos ventanas complementarias: lo que acaba de explotar y lo que lleva unos
# meses subiendo fuerte. Se deduplica por nombre.
CONSULTAS = [
    {"etiqueta": "nuevos", "dias": 30, "estrellas_min": 100},
    {"etiqueta": "recientes", "dias": 180, "estrellas_min": 1000},
]

# Un repo sin commits recientes no es una tendencia, es un pico que ya pasó.
DIAS_SIN_ACTIVIDAD = 21

MAX_CANDIDATOS = 25
MAX_ELEGIDOS = 8


def _formatear_estrellas(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".", ",")
    return str(n)


def _fecha_github(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 1. Búsqueda
# --------------------------------------------------------------------------

def _buscar(consulta: str, token: str, por_pagina: int = 50) -> list[dict]:
    url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode({
            "q": consulta,
            "sort": "stars",
            "order": "desc",
            "per_page": por_pagina,
        })
    )
    cabeceras = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": AGENTE,
    }
    # Sin token la búsqueda son 10 peticiones/minuto; con él, 30. En Actions
    # GITHUB_TOKEN viene dado, así que normalmente hay token.
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"

    peticion = urllib.request.Request(url, headers=cabeceras)
    with urllib.request.urlopen(peticion, timeout=60) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))
    return datos.get("items") or []


def obtener_candidatos(token: str = "") -> tuple[list[dict], dict]:
    """Busca, filtra y rankea por estrellas/día. Devuelve (candidatos, diagnostico)."""
    ahora = datetime.now(timezone.utc)
    por_nombre: dict[str, dict] = {}
    diag = {
        "devueltos": 0, "fork": 0, "archivado": 0,
        "inactivo": 0, "sinFecha": 0, "ok": 0, "consultasFallidas": 0,
    }

    for consulta in CONSULTAS:
        desde = (ahora - timedelta(days=consulta["dias"])).date().isoformat()
        q = f"created:>={desde} stars:>={consulta['estrellas_min']}"
        try:
            items = _buscar(q, token)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detalle = exc.read().decode("utf-8", "replace")[:200] if hasattr(exc, "read") else exc
            log(f"  AVISO búsqueda '{consulta['etiqueta']}' falló -> {detalle}")
            diag["consultasFallidas"] += 1
            continue

        log(f"  {consulta['etiqueta']}: {len(items)} repos ({q})")
        diag["devueltos"] += len(items)

        for repo in items:
            nombre = repo.get("full_name")
            if not nombre or nombre in por_nombre:
                continue
            if repo.get("fork"):
                diag["fork"] += 1
                continue
            if repo.get("archived"):
                diag["archivado"] += 1
                continue

            creado = _fecha_github(repo.get("created_at"))
            empujado = _fecha_github(repo.get("pushed_at"))
            if creado is None:
                diag["sinFecha"] += 1
                continue
            if empujado and (ahora - empujado).days > DIAS_SIN_ACTIVIDAD:
                diag["inactivo"] += 1
                continue

            estrellas = repo.get("stargazers_count") or 0
            edad_dias = max((ahora - creado).days, 1)
            diag["ok"] += 1

            por_nombre[nombre] = {
                "nombre": nombre,
                "url": repo.get("html_url") or f"https://github.com/{nombre}",
                "descripcion": (repo.get("description") or "").strip(),
                "lenguaje": repo.get("language") or "",
                "temas": (repo.get("topics") or [])[:6],
                "estrellas": estrellas,
                "edad_dias": edad_dias,
                "estrellas_dia": round(estrellas / edad_dias, 1),
            }

    candidatos = sorted(
        por_nombre.values(), key=lambda r: r["estrellas_dia"], reverse=True
    )[:MAX_CANDIDATOS]
    return candidatos, diag


# --------------------------------------------------------------------------
# 2. Resumen con Claude
# --------------------------------------------------------------------------

SYSTEM = "\n".join([
    "Eres un analista que prepara un briefing diario para un desarrollador de software.",
    "Recibes repositorios de GitHub que están ganando estrellas rápido, con su descripción y su lenguaje.",
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
    "NO inventes números de estrellas ni fechas: no te los pedimos y se añaden después.",
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
            f"    lenguaje: {c['lenguaje'] or 'n/d'} | estrellas: {c['estrellas']} "
            f"| ritmo: {c['estrellas_dia']}/día | edad: {c['edad_dias']} días",
            f"    temas: {', '.join(c['temas'])}" if c["temas"] else None,
            f"    descripcion: {c['descripcion']}" if c["descripcion"] else "    descripcion: (sin descripción)",
        ]))
        for i, c in enumerate(candidatos)
    )

    return {
        "model": MODELO,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [
            {"role": "user", "content": f"Repositorios candidatos:\n\n{listado}"}
        ],
        "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}},
    }


# --------------------------------------------------------------------------
# 3. Mensaje
# --------------------------------------------------------------------------

def formatear_bloques(datos: dict, candidatos: list[dict], fecha_texto: str) -> list[str]:
    """Une lo que dijo Claude con los números reales, que salen de la búsqueda.

    Los datos duros (estrellas, ritmo) se toman SIEMPRE de `candidatos`, nunca de
    la respuesta del modelo: así no hay forma de que aparezca una cifra inventada.
    """
    por_nombre = {c["nombre"]: c for c in candidatos}

    bloques = [
        f"<b>🚀 Repos en tendencia — {escapar(fecha_texto)}</b>\n\n"
        f"<i>{escapar(datos.get('resumen_global'))}</i>"
    ]

    i = 0
    for elegido in datos.get("repos") or []:
        datos_reales = por_nombre.get(elegido.get("nombre"))
        if not datos_reales:
            log(f"  AVISO el modelo devolvió '{elegido.get('nombre')}', que no era candidato: se descarta")
            continue

        i += 1
        estrellas = _formatear_estrellas(datos_reales["estrellas"])
        bloques.append("\n".join([
            f"<b>{i}. {escapar(datos_reales['nombre'])}</b>"
            f" · {escapar(elegido.get('categoria'))}",
            f"⭐ {estrellas} · {datos_reales['estrellas_dia']}/día"
            + (f" · {escapar(datos_reales['lenguaje'])}" if datos_reales["lenguaje"] else ""),
            "",
            f"<b>Para qué sirve:</b> {escapar(elegido.get('para_que_sirve'))}",
            f"<b>Por qué sube:</b> {escapar(elegido.get('por_que_sube'))}",
            f"<a href=\"{escapar(datos_reales['url'])}\">Ver en GitHub</a>",
        ]))

    if i == 0:
        return [
            f"<b>🚀 Repos en tendencia — {escapar(fecha_texto)}</b>\n\n"
            "No se encontraron repositorios destacables con los criterios actuales."
        ]

    return bloques


# --------------------------------------------------------------------------
# Orquestación de la sección
# --------------------------------------------------------------------------

def generar(fecha_texto: str, api_key: str, solo_candidatos: bool = False) -> list[str]:
    """Devuelve los bloques de Telegram de la sección de repos."""
    token = os.environ.get("GITHUB_TOKEN", "")
    log("Buscando repos en tendencia..." + ("" if token else " (sin GITHUB_TOKEN: límite más bajo)"))

    candidatos, diag = obtener_candidatos(token)
    log(f"Filtrado repos: {json.dumps(diag)}")
    log(f"Candidatos seleccionados: {len(candidatos)}")

    # Antes de cualquier atajo: si no respondió ninguna consulta es un fallo real,
    # y tiene que verse también en --dry-run.
    if diag["consultasFallidas"] == len(CONSULTAS):
        raise RuntimeError("Todas las búsquedas de GitHub fallaron: ninguna consulta respondió.")

    if solo_candidatos:
        for i, c in enumerate(candidatos[:15], start=1):
            log(f"  [{i}] {c['estrellas_dia']:>7}/día  ⭐{c['estrellas']:<7} {c['nombre']} — {c['descripcion'][:60]}")
        return []

    if not candidatos:
        return [
            f"<b>🚀 Repos en tendencia — {escapar(fecha_texto)}</b>\n\n"
            "No se encontraron repositorios destacables con los criterios actuales."
        ]

    log(f"Explicando con {MODELO}...")
    respuesta = llamar_claude(construir_body(candidatos), api_key)
    registrar_uso(respuesta)
    return formatear_bloques(extraer_datos(respuesta), candidatos, fecha_texto)
