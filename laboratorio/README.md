# Laboratorio

El inventario de lo que estás construyendo. Un archivo `.md` por proyecto o
experimento. La sección `laboratorio` del digest lo lee cada mañana y lo cruza
con las noticias y los repos del día para avisarte cuando aparece algo que
desbloquea algo tuyo.

## Lo único que hay que entender

**El cruce se hace contra tus BLOQUEOS, no contra la descripción del proyecto.**

Si un archivo dice *"notic-ia: un digest de noticias de IA"*, no va a matchear
nunca nada: es una descripción, y las descripciones no tienen huecos donde
encaje una herramienta. Si dice *"no puedo detectar repos que suben rápido sin
raspar HTML frágil"*, entonces el día que salga una API de trending, o una
librería que lo resuelva, el sistema te lo va a decir.

Una herramienta encaja en un **problema**, no en un proyecto. Así que la parte
que hay que escribir con cuidado es `## Bloqueos`, y conviene ser concreto y
un poco quejica: *lo que te da rabia no poder hacer* es exactamente la señal.

Lo mismo vale para `## Ideas`: cosas que te gustaría construir y hoy no podés,
o no sabés cómo. Un proyecto que ni siquiera existe puede tener bloqueos.

## Cómo añadir uno

Copiá [`PLANTILLA.md`](PLANTILLA.md) a `laboratorio/<nombre>.md` y rellenalo.
No hay que registrar el archivo en ningún sitio: se leen todos los `.md` de esta
carpeta menos este README y la plantilla.

## Qué esperar

**Silencio la mayoría de los días.** La sección sólo manda mensaje cuando hay al
menos un cruce real. Si te escribe todos los días, el listón está bajo y hay que
endurecerlo — igual que en la sección de resumen.

Y al revés: si nunca te escribe, casi seguro que los bloqueos están escritos
como descripciones. Releelos y preguntate si cada uno nombra algo que hoy **no
podés hacer**.
