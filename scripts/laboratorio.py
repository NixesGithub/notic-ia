"""Cruza lo que sale hoy con lo que estás construyendo.

Las otras secciones miran el mundo. Esta mira tu inventario —los `.md` de
`laboratorio/`— y contesta una sola pregunta: **¿algo de lo de hoy desbloquea
algo tuyo?**

El cruce se hace contra los BLOQUEOS declarados, no contra la descripción de
los proyectos. Una herramienta encaja en un problema, no en un proyecto: si el
inventario sólo dice qué hace cada cosa, no habrá nunca dónde encajar nada. Eso
está explicado en laboratorio/README.md, que es lo que hay que leer antes de
tocar el formato.

Diferencia importante con las otras secciones: **si no hay cruce, no manda
mensaje**. No un mensaje diciendo que no hay nada — ninguno. Un aviso que llega
todos los días deja de ser un aviso.
"""

from __future__ import annotations

import pathlib

from comun import escapar, log, modelo_de, preguntar

DIRECTORIO = pathlib.Path(__file__).resolve().parent.parent / "laboratorio"

# No son proyectos: son la documentación de la carpeta.
IGNORADOS = {"README.md", "PLANTILLA.md"}

MAX_PROYECTOS = 20
MAX_CARACTERES = 6000  # por proyecto; corta inventarios desbocados


def cargar_inventario() -> list[dict]:
    """Lee laboratorio/*.md. El contenido va al modelo tal cual, sin parsear.

    No se interpretan las secciones a propósito: cualquier formato que escriba
    una persona vale, y un parser sería una fuente de fallos silenciosos.
    """
    if not DIRECTORIO.is_dir():
        return []

    proyectos = []
    for ruta in sorted(DIRECTORIO.glob("*.md")):
        if ruta.name in IGNORADOS:
            continue
        try:
            texto = ruta.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log(f"  AVISO no se pudo leer {ruta.name}: {exc}")
            continue
        if not texto:
            continue
        proyectos.append({"nombre": ruta.stem, "texto": texto[:MAX_CARACTERES]})

    return proyectos[:MAX_PROYECTOS]


SYSTEM = "\n".join([
    "Ayudas a un inventor que construye sistemas para sí mismo a detectar cuándo algo que ha",
    "salido hoy desbloquea algo que él ya quiere hacer.",
    "",
    "Recibes tres cosas: su INVENTARIO de proyectos (cada uno con sus bloqueos e ideas), los",
    "titulares de noticias del día, y los repositorios de GitHub en tendencia.",
    "",
    "Tu tarea: encontrar cruces REALES entre lo de hoy y un bloqueo o una idea concreta del",
    "inventario. Un cruce es real cuando podrías escribir la frase:",
    "'esto que salió hoy resuelve, o acerca mucho, este problema concreto que tenés anotado'.",
    "",
    "EL SESGO CORRECTO ES NO ENCONTRAR NADA. Lo normal, con diferencia, es que un día cualquiera",
    "no traiga nada que encaje en los bloqueos de nadie. Si no hay un cruce claro, devuelve el",
    "array vacío. Eso es el resultado esperado la mayoría de los días, no un fallo.",
    "",
    "Lo que NO es un cruce, y hay que descartar:",
    "- Que la herramienta sea 'del mismo tema' que el proyecto. Que las dos cosas hablen de IA,",
    "  de scraping o de agentes no es un cruce: el cruce es contra un bloqueo NOMBRADO.",
    "- Que 'podría servirle para algo'. Si no señalás qué bloqueo concreto ataca, no es un cruce.",
    "- Que sea interesante en general. Para eso ya hay otra sección; esta no la duplica.",
    "- Que resuelva el problema sólo si él reescribe medio proyecto. Si el coste de adoptarlo es",
    "  mayor que el del bloqueo, no compensa y no es un cruce.",
    "",
    "Máximo 3 cruces. Lo normal es 0.",
    "",
    "Reglas de salida:",
    "- Escribe SIEMPRE en español de España, directo y concreto.",
    '- "proyecto": copiá EXACTAMENTE el nombre del proyecto del inventario. No inventes proyectos.',
    '- "bloqueo": la frase del inventario que este cruce ataca, resumida en una línea. Tiene que',
    "  ser algo que esté escrito en su inventario, no algo que te parezca a ti que le pasa.",
    '- "que_lo_desbloquea": qué es exactamente lo que salió hoy y por qué resuelve ese bloqueo.',
    "  Concreto: nombres, versiones, cifras.",
    '- "como_implementarlo": los pasos concretos para meterlo en ESE proyecto, teniendo en cuenta',
    "  el stack que declara el inventario. Un comando, un archivo a tocar, un orden de trabajo.",
    "  Si no podés proponer un primer paso real, el cruce no era bueno: quítalo.",
    '- "confianza": "alta" si resuelve el bloqueo de forma directa; "media" si lo acerca o hay',
    "  que comprobar algo primero.",
    '- "url" y "fuente": cópialos EXACTAMENTE del candidato que disparó el cruce.',
    "- Ordena de más a menos impacto.",
])

ESQUEMA = {
    "type": "object",
    "properties": {
        "cruces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "proyecto": {"type": "string"},
                    "bloqueo": {"type": "string"},
                    "que_lo_desbloquea": {"type": "string"},
                    "como_implementarlo": {"type": "string"},
                    "confianza": {"type": "string", "enum": ["alta", "media"]},
                    "fuente": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["proyecto", "bloqueo", "que_lo_desbloquea",
                             "como_implementarlo", "confianza", "fuente", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cruces"],
    "additionalProperties": False,
}


def construir_peticion(proyectos: list[dict], noticias: list[dict], repos_: list[dict]) -> dict:
    partes = ["INVENTARIO:\n\n" + "\n\n---\n\n".join(p["texto"] for p in proyectos)]

    if noticias:
        partes.append("NOTICIAS DE HOY:\n\n" + "\n\n".join(
            "\n".join(filter(None, [
                f"[N{i + 1}] {c['titulo']}",
                f"    fuente: {c['fuente']} | url: {c['url']}",
                f"    extracto: {c['extracto']}" if c.get("extracto") else None,
            ]))
            for i, c in enumerate(noticias)
        ))

    if repos_:
        partes.append("REPOS EN TENDENCIA:\n\n" + "\n\n".join(
            "\n".join(filter(None, [
                f"[R{i + 1}] {c['nombre']}",
                f"    lenguaje: {c['lenguaje'] or 'n/d'} | url: {c['url']}",
                f"    descripcion: {c['descripcion']}" if c.get("descripcion") else None,
            ]))
            for i, c in enumerate(repos_)
        ))

    return {"system": SYSTEM, "usuario": "\n\n".join(partes), "esquema": ESQUEMA}


ICONOS = {"alta": "🎯", "media": "🤔"}


def formatear_bloques(datos: dict, proyectos: list[dict], fecha_texto: str) -> list[str]:
    """Devuelve [] cuando no hay cruces: sin cruces no se manda nada."""
    nombres = {p["nombre"] for p in proyectos}
    cruces = []

    for c in datos.get("cruces") or []:
        if c.get("proyecto") not in nombres:
            log(f"  AVISO cruce sobre '{c.get('proyecto')}', que no está en el inventario: se descarta")
            continue
        cruces.append(c)

    if not cruces:
        return []

    bloques = [
        f"<b>🔧 Encaja con lo tuyo — {escapar(fecha_texto)}</b>\n\n"
        f"<i>{len(cruces)} cosa(s) de hoy tocan algo que tenés anotado.</i>"
    ]

    for i, c in enumerate(cruces, start=1):
        bloques.append("\n".join([
            f"{ICONOS.get(c.get('confianza'), '•')} <b>{i}. {escapar(c.get('proyecto'))}</b>",
            f"<b>Bloqueo:</b> {escapar(c.get('bloqueo'))}",
            f"<b>Lo desbloquea:</b> {escapar(c.get('que_lo_desbloquea'))}",
            f"<b>Cómo meterlo:</b> {escapar(c.get('como_implementarlo'))}",
            f"<a href=\"{escapar(c.get('url'))}\">{escapar(c.get('fuente'))}</a>",
        ]))

    return bloques


def generar(
    noticias: list[dict], repos_: list[dict], fecha_texto: str,
    api_key: str, solo_candidatos: bool = False,
) -> list[str]:
    proyectos = cargar_inventario()

    if not proyectos:
        # Sin inventario no hay nada contra qué cruzar. No es un error —el
        # laboratorio puede estar vacío— pero conviene que se vea en el log.
        log(f"Inventario vacío en {DIRECTORIO}: no hay nada contra qué cruzar. "
            "Añadí un .md por proyecto (mirá laboratorio/README.md).")
        return []

    if not noticias and not repos_:
        raise RuntimeError(
            "No hay material: las secciones de noticias y repos fallaron o no se pidieron."
        )

    log(f"Inventario: {len(proyectos)} proyecto(s) -> {', '.join(p['nombre'] for p in proyectos)}")
    log(f"Material: {len(noticias)} noticias + {len(repos_)} repos")

    if solo_candidatos:
        log("  (en --dry-run no se llama al modelo; el cruce lo hace él)")
        return []

    log(f"Buscando cruces con {modelo_de('laboratorio')}...")
    datos = preguntar("laboratorio", **construir_peticion(proyectos, noticias, repos_), clave=api_key)
    bloques = formatear_bloques(datos, proyectos, fecha_texto)
    log(f"Cruces encontrados: {max(len(bloques) - 1, 0)}"
        + ("" if bloques else " (no se manda mensaje)"))
    return bloques
