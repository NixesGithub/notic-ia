"""El resumen final: qué de todo lo de hoy te cambia algo de verdad.

Las otras dos secciones cuentan **qué pasó**. Esta contesta otra pregunta:
**qué de eso te habilita a hacer algo que ayer no podías, o a hacerlo mucho
mejor.** Casi todos los días la respuesta honesta es "nada", y este módulo está
construido para poder decirlo: `puntos` puede venir vacío y el mensaje lo dice
sin rellenar.

Lee los candidatos **crudos** de las otras dos secciones, no lo que ya
eligieron. Es a propósito: el prompt de noticias rankea la financiación y las
adquisiciones como criterio de importancia, justo lo que aquí se descarta, así
que partir de su top 10 heredaría el filtro equivocado. Lo relevante para este
perfil puede estar en el puesto 30 de los candidatos.
"""

from __future__ import annotations

from comun import MODELO, escapar, extraer_datos, llamar_claude, log, registrar_uso

# Editá esto y cambia todo el criterio del resumen. Es el único sitio donde vive
# "para quién" se filtra.
PERFIL = "\n".join([
    "Desarrollador de software. También emprendedor (proyectos propios, en solitario o en equipo",
    "muy pequeño), músico e inventor: construye sistemas para sí mismo, y le interesa todo lo que",
    "le permita fabricar algo que antes no podía fabricar.",
    "",
    "LE INTERESA, porque extiende lo que puede hacer:",
    "- Modelos nuevos o versiones nuevas, cuando cambian de verdad lo que se puede pedirles:",
    "  capacidades, límites de contexto, latencia, precio, disponibilidad de API, cuotas.",
    "- Herramientas de programación asistida por IA: agentes de código, editores, CLIs, extensiones,",
    "  protocolos como MCP, formas nuevas de integrar modelos en el trabajo diario.",
    "- Técnicas y procedimientos: maneras de usar estas herramientas que cambian el resultado",
    "  (patrones de prompting con evidencia, arquitecturas de agentes, evaluación, RAG, fine-tuning",
    "  accesible). Vale también si es un artículo técnico y no un lanzamiento.",
    "- Software libre o autoalojable que sustituye a algo de pago, o que baja mucho la barrera",
    "  de construir cosas solo.",
    "- Herramientas de IA aplicadas a música: generación, separación de pistas, masterización,",
    "  transcripción, plugins, síntesis de voz cantada, producción.",
    "- Cambios que afectan a lo que un desarrollador o un proyecto pequeño puede lanzar:",
    "  licencias, condiciones de uso, precios que abren o cierran puertas, cambios legales con",
    "  efecto práctico e inmediato sobre qué se puede publicar.",
    "- Agentes que operan solos: sistemas que navegan, usan el ordenador, ejecutan tareas largas",
    "  sin supervisión. Es una capacidad nueva, no una noticia de producto: cuenta aunque no",
    "  pueda usarlo hoy mismo, porque cambia lo que es construible.",
    "- Herramientas de fabricación y prototipado en general, no sólo software: impresión 3D,",
    "  electrónica accesible, CAD asistido, simulación, hardware barato. Le sirve todo lo que",
    "  baje la barrera de construir una cosa que antes requería un taller o un equipo.",
    "- Disputas legales o regulatorias que afecten DIRECTAMENTE a los laboratorios cuyas",
    "  herramientas usa (OpenAI, Anthropic, Google, Meta...) cuando puedan cambiar el acceso,",
    "  los precios, las condiciones de uso o la disponibilidad de sus modelos. Esta es una",
    "  EXCEPCIÓN deliberada a la regla de abajo sobre política de empresas: no le interesa el",
    "  conflicto en sí, le interesa si mañana puede seguir usando la herramienta igual.",
    "",
    "NO LE INTERESA, y hay que descartarlo aunque sea la noticia más grande del día:",
    "- Rondas de financiación, valoraciones, inversores, salidas a bolsa, resultados trimestrales.",
    "- Fusiones y adquisiciones, salvo que el titular diga explícitamente que un producto que él",
    "  usaría se cierra, se abre o cambia de condiciones.",
    "- Fichajes, ceses y movimientos de ejecutivos; política interna de las empresas.",
    "  (Ojo: esto NO cubre las disputas legales o regulatorias de arriba, que sí le interesan",
    "  cuando pueden cambiar el acceso a una herramienta que usa.)",
    "- Acciones, bolsa, predicciones de mercado, 'deberías comprar X'.",
    "- Opinión y futurología sin hecho nuevo: 'la IA cambiará el trabajo', 'los agentes son el futuro'.",
    "- Estudios sobre el impacto social o laboral de la IA, salvo que cambien algo que él haga.",
    "- Aplicaciones de IA en sectores ajenos (sanidad, defensa, agricultura, administración pública)",
    "  sin herramienta reutilizable por él.",
    "- Anuncios de conferencias, ponentes, premios y eventos.",
])

# Casos de calibración: noticias que el dueño de este digest señaló como
# claramente relevantes, y el patrón que las hace relevantes. Están escritas como
# PATRONES ilustrados con sus ejemplos, no como nombres sueltos, para que el
# criterio transfiera a cosas que todavía no existen.
#
# Si algún día llega algo que no querías, o se pierde algo que sí, el arreglo es
# añadir una línea aquí o en PERFIL. No hay entrenamiento detrás: esto es el
# prompt, y es lo único que decide.
EJEMPLOS = "\n".join([
    "CASOS QUE SÍ ERAN RELEVANTES (calibrá el listón con estos):",
    "",
    "- La aparición de agentes que operan solos en internet: sistemas que navegan, usan el",
    "  ordenador y encadenan tareas largas sin que nadie los supervise. Relevante porque es una",
    "  capacidad nueva que cambia qué cosas son construibles, no porque sea un producto vistoso.",
    "- Cada versión nueva de los modelos grandes (OpenAI, Anthropic, Google...) cuando trae un",
    "  cambio real de capacidad, contexto, precio o velocidad. Una versión que sólo es 'mejor en",
    "  benchmarks' sin consecuencia práctica NO cuenta.",
    "- La salida de herramientas que cambian cómo se programa: un agente de código que corre en la",
    "  terminal, un editor con el modelo integrado, un protocolo para conectar modelos a",
    "  herramientas. Relevante porque se instala hoy y cambia el trabajo diario de mañana.",
    "- Disputas legales o regulatorias que afectan a un laboratorio cuya herramienta usa —por",
    "  ejemplo un gobierno enfrentado a Anthropic o a OpenAI— cuando puedan cambiar el acceso,",
    "  el precio o las condiciones de uso. El conflicto no importa; importa si mañana la",
    "  herramienta sigue estando y en qué términos.",
    "- Cualquier herramienta, de software o de fabricación, que le permita construir algo que",
    "  antes necesitaba un taller, un equipo o un presupuesto que no tiene.",
    "",
    "CASOS QUE NO ERAN RELEVANTES, por más grandes que fueran:",
    "",
    "- 'La empresa X cerró una ronda de N millones' / 'ahora vale N mil millones'. Es la noticia",
    "  más repetida del sector y no cambia absolutamente nada de lo que él puede hacer.",
    "- Cambios de ejecutivos, reorganizaciones, peleas societarias sin efecto sobre el producto.",
    "- 'La IA transformará el empleo': ensayo sin hecho nuevo ni herramienta que tocar.",
])

SYSTEM = "\n".join([
    "Filtras el ruido de un briefing diario de IA para UNA persona concreta. Este es su perfil:",
    "",
    PERFIL,
    "",
    EJEMPLOS,
    "",
    "Recibes dos listas: titulares de noticias del día y repositorios de GitHub en tendencia.",
    "",
    "Tu tarea: quedarte SÓLO con lo que le cambia algo a esta persona en su día a día, y explicar",
    "qué puede hacer con ello. La pregunta que contestas en cada punto es:",
    "'¿qué puedo hacer hoy que ayer no podía, o que ahora puedo hacer mucho mejor?'",
    "",
    "EL SESGO CORRECTO ES DESCARTAR. Un día normal no trae nada que cambie de verdad cómo",
    "trabaja alguien. Si no hay nada que supere el listón, devuelve el array vacío y dilo en el",
    "veredicto. Eso es una respuesta correcta y útil, no un fallo. NO rellenes con lo menos malo:",
    "un resumen que todos los días encuentra cinco cosas importantes no vale para nada, porque",
    "deja de distinguir el día que sí importa.",
    "",
    "Máximo 5 puntos. Lo normal es entre 0 y 3.",
    "",
    "Antes de incluir algo, comprueba que pasa este listón:",
    "- ¿Hay un hecho concreto y nuevo, o es un titular sobre una intención, una opinión o una cifra",
    "  de dinero? Si no hay hecho técnico, fuera.",
    "- ¿Podría actuar sobre esto en la próxima semana: probarlo, instalarlo, cambiar cómo hace algo?",
    "  Si la respuesta es 'no, es contexto general', fuera.",
    "- ¿Es específico de él —programación, construir productos pequeños, música— o es interés",
    "  general sobre el sector? Si es general, fuera.",
    "",
    "Reglas de salida:",
    "- Escribe SIEMPRE en español de España, directo y sin adornos.",
    '- "titulo": máximo 70 caracteres. El hecho, no el gancho.',
    '- "que_cambia": 1 o 2 frases con lo que es distinto ahora. Concreto: versiones, cifras,',
    "  límites, precios, nombres. Nada de 'esto abre nuevas posibilidades'.",
    '- "que_podes_hacer": 1 o 2 frases con la acción concreta que puede tomar. Un comando, una',
    "  prueba, un cambio en su flujo de trabajo, algo que instalar. Si no se te ocurre ninguna",
    "  acción concreta, entonces el punto no pasaba el listón: quítalo.",
    '- "faceta": exactamente una de "programador", "emprendedor", "músico", "inventor".',
    '  Usá "inventor" cuando lo que cambia es la capacidad de FABRICAR algo nuevo, sea software',
    "  o no, más que la de programar mejor.",
    '- "url" y "fuente": cópialos EXACTAMENTE del candidato. No inventes URLs.',
    "- Ordena de más a menos impacto para él.",
    '- "veredicto": una frase honesta sobre el día. Si no hay nada, dilo claramente',
    "  (ej. 'Día sin novedades que cambien nada de tu trabajo').",
])

ESQUEMA = {
    "type": "object",
    "properties": {
        "puntos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "que_cambia": {"type": "string"},
                    "que_podes_hacer": {"type": "string"},
                    "faceta": {"type": "string",
                               "enum": ["programador", "emprendedor", "músico", "inventor"]},
                    "fuente": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["titulo", "que_cambia", "que_podes_hacer", "faceta", "fuente", "url"],
                "additionalProperties": False,
            },
        },
        "veredicto": {"type": "string"},
    },
    "required": ["puntos", "veredicto"],
    "additionalProperties": False,
}

ICONOS = {"programador": "💻", "emprendedor": "🚀", "músico": "🎸", "inventor": "🔧"}


def construir_body(noticias: list[dict], repos_: list[dict], fecha_texto: str) -> dict:
    partes = []

    if noticias:
        partes.append("NOTICIAS:\n\n" + "\n\n".join(
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
                f"    lenguaje: {c['lenguaje'] or 'n/d'} | +{c['ganadas']} estrellas {c['periodo']}"
                f" | url: {c['url']}",
                f"    descripcion: {c['descripcion']}" if c.get("descripcion") else None,
            ]))
            for i, c in enumerate(repos_)
        ))

    return {
        "model": MODELO,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [
            {"role": "user", "content": f"Material del {fecha_texto}:\n\n" + "\n\n".join(partes)}
        ],
        "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}},
    }


def formatear_bloques(datos: dict, fecha_texto: str) -> list[str]:
    puntos = datos.get("puntos") or []

    cabecera = f"<b>🎯 Lo que te cambia algo — {escapar(fecha_texto)}</b>"
    if not puntos:
        # Un día sin nada es una respuesta legítima, no un fallo.
        return [f"{cabecera}\n\n<i>{escapar(datos.get('veredicto'))}</i>"]

    bloques = [f"{cabecera}\n\n<i>{escapar(datos.get('veredicto'))}</i>"]

    for i, p in enumerate(puntos, start=1):
        icono = ICONOS.get(p.get("faceta"), "•")
        bloques.append("\n".join([
            f"{icono} <b>{i}. {escapar(p.get('titulo'))}</b>",
            f"<b>Qué cambia:</b> {escapar(p.get('que_cambia'))}",
            f"<b>Qué podés hacer:</b> {escapar(p.get('que_podes_hacer'))}",
            f"<a href=\"{escapar(p.get('url'))}\">{escapar(p.get('fuente'))}</a>",
        ]))

    return bloques


def generar(
    noticias: list[dict], repos_: list[dict], fecha_texto: str,
    api_key: str, solo_candidatos: bool = False,
) -> list[str]:
    if not noticias and not repos_:
        raise RuntimeError(
            "No hay material: las dos secciones anteriores fallaron o no se pidieron."
        )

    log(f"Material: {len(noticias)} noticias + {len(repos_)} repos")

    if solo_candidatos:
        log("  (en --dry-run no se llama al modelo; el filtrado lo hace él)")
        return []

    log(f"Filtrando con {MODELO}...")
    respuesta = llamar_claude(construir_body(noticias, repos_, fecha_texto), api_key)
    registrar_uso(respuesta)
    datos = extraer_datos(respuesta)
    log(f"Puntos que pasan el listón: {len(datos.get('puntos') or [])}")
    return formatear_bloques(datos, fecha_texto)
