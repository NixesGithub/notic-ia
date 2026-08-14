# Notic-IA 

A custom newspaper every morning with news of my preference.


Instancia local de [n8n](https://n8n.io) levantada con Docker Compose, que importa
automáticamente sus workflows al arrancar.

| Workflow | Qué hace |
|---|---|
| **Hola Mundo** | Ejemplo mínimo para verificar que la instancia funciona |
| **Test Telegram** | Manda un mensaje de prueba a tu chat. Sirve para validar credencial + chat ID sin gastar API de Anthropic |
| **Noticias IA diarias** | Cada mañana a las 11:00 (hora de España) lee 9 fuentes de noticias de IA, se queda con las del día anterior, las rankea, pide a Claude el top 10 resumido y lo manda por Telegram |

## Estructura

```
n8n-mvp/
├── docker-compose.yml      # servicio n8n + servicio de importación de workflows
├── .env                    # clave de cifrado, puerto, zona horaria, chat de Telegram
├── .env.example            # plantilla de .env
└── workflows/
    ├── hola-mundo.json
    └── noticias-ia.json
```

## Levantar

```bash
docker compose up -d
```

Abrí http://localhost:5678. La primera vez n8n pide crear una cuenta de **owner**
(email + contraseña); son locales, no se envían a ningún lado.

---

# Workflow: Hola Mundo

1. En la lista de workflows abrí **Hola Mundo**.
2. Click en **Execute workflow**.
3. Abrí el nodo *Formatear salida*: en la salida vas a ver

```json
{
  "mensaje": "¡Hola Mundo desde n8n!",
  "fecha": "2026-08-10T15:30:00.000+02:00",
  "resumen": "¡Hola Mundo desde n8n! (generado el 2026-08-10T15:30:00.000+02:00)"
}
```

Tres nodos: **Manual Trigger** → **Set** (arma `mensaje` y `fecha`) → **Code** (formatea el `resumen`).

También podés ejecutarlo desde la CLI:

```bash
docker compose exec -e N8N_RUNNERS_BROKER_PORT=5699 n8n n8n execute --id hola-mundo-mvp
```

(El `N8N_RUNNERS_BROKER_PORT` distinto es necesario porque la instancia principal ya tiene
tomado el puerto 5679 del task broker.)

---

# Workflow: Noticias IA diarias

## Qué hace

```
Cada día 11:00 (Europe/Madrid)
   └─ Fuentes ......... genera las 9 URLs de RSS (las de Google News con el rango de fechas del día anterior)
      └─ Leer RSS ..... lee los 9 feeds (~280 noticias)
         └─ Seleccionar del día anterior
                        filtra por fecha, deduplica entre medios, penaliza ruido y rankea → top 40
            └─ ¿Hay noticias?
               ├─ sí → Resumir con Claude (API de Anthropic, salida JSON estructurada)
               │        └─ Formatear mensaje (HTML de Telegram, troceado a 3800 caracteres)
               └─ no → Sin noticias
                        └─ Enviar a Telegram
```

**Nada de scraping de HTML.** Los medios publican RSS justamente para esto: es estable y no se
rompe cuando cambian el CSS. Las fuentes (con su peso en el ranking) son:

| Fuente | Peso | Fuente | Peso |
|---|---|---|---|
| TechCrunch AI | 3 | Wired AI | 2 |
| The Verge AI | 3 | VentureBeat AI | 2 |
| Ars Technica AI | 3 | Hacker News (filtrado por IA) | 2 |
| MIT Technology Review AI | 3 | Google News EN + ES | 1 |

### Cómo se decide "lo más importante"

Dos capas. La primera es determinista, en el nodo *Seleccionar del día anterior*:

- **Filtro de fecha**: sólo noticias publicadas entre las 00:00 y las 23:59 del día anterior, hora de España.
- **Deduplicación**: se normaliza el título (sin acentos ni puntuación) y se agrupan las 9 primeras
  palabras. Si la misma noticia sale en 3 medios, cuenta como una sola pero **suma puntos**: aparecer
  en varios sitios es la mejor señal de importancia que hay.
- **Puntuación**: `peso_fuente × 2 + palabras_clave + apariciones × 2`, penalizando 12 puntos el
  contenido bursátil sindicado (Motley Fool, Zacks, Benzinga…) que inunda las búsquedas de "IA".
- Se queda con los **40 mejores**.

La segunda capa es **Claude** (`claude-opus-5`), que recibe esos 40 titulares y elige el top 10 real,
lo resume en español y le pone una nota de importancia. Devuelve JSON validado contra un esquema
(`output_config.format`), así que la salida siempre es parseable.

## Puesta en marcha

### 1. Credencial de Anthropic

1. Sacá una API key en https://console.anthropic.com → *API Keys*.
2. En n8n: **Credentials** → **New** → buscá **Anthropic** → pegá la key → *Save*.

3. Abrí el nodo *Resumir con Claude* y **seleccioná la credencial en el desplegable**, aunque parezca
   que ya está puesta. Después exportá el workflow sobre `workflows/noticias-ia.json`.

El paso 3 no es opcional. Que exista una credencial del tipo correcto **no alcanza**: el JSON tiene
que referenciarla por id. La UI la autoselecciona sola cuando hay una única credencial del tipo, pero
un workflow **importado** desde JSON sin bloque `credentials` falla con `Credentials not found`. El
bloque se ve así dentro del nodo:

```json
"credentials": { "anthropicApi": { "id": "daNyZM3L7Inr38Hz", "name": "Anthropic account" } }
```

Ese id es **local a esta instalación**: si borrás el volumen o llevás el proyecto a otra máquina, hay
que rehacer la credencial y volver a seleccionarla.

**Coste**: unos 40 titulares de entrada y ~1.500 tokens de salida por día. Con `claude-opus-5`
($5 por millón de tokens de entrada, $25 de salida) sale del orden de **3–6 céntimos al día**.
Si querés abaratar, cambiá `model: 'claude-opus-5'` por `'claude-sonnet-5'` en el nodo
*Seleccionar del día anterior*.

### 2. Bot de Telegram

1. En Telegram hablá con **@BotFather** → `/newbot` → seguí los pasos → te da un **token**.
2. En n8n: **Credentials** → **New** → **Telegram API** → pegá el token → *Save*.
3. Escribile algo a tu bot (si no, no puede contestarte) y abrí en el navegador:
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   El número que aparece en `result[0].message.chat.id` es tu **chat ID**.
4. Poné ese número en `.env`:

```
TELEGRAM_CHAT_ID=123456789
```

5. Recreá el contenedor para que tome la variable: `docker compose up -d`
6. Abrí los nodos *Enviar a Telegram* (en **Noticias IA diarias** y en **Test Telegram**),
   seleccioná la credencial en el desplegable y exportá los workflows sobre `workflows/`.
   Igual que con Anthropic: si el JSON no referencia la credencial por id, falla con
   `Node does not have any credentials set` aunque la credencial exista.

Para comprobar que todo esto quedó bien **sin gastar API de Anthropic**, ejecutá el workflow
**Test Telegram**: manda un mensaje de prueba y valida credencial, chat ID y formato HTML de una.

### 3. Activar

Ya viene activado: `noticias-ia.json` tiene `"active": true` y el servicio `n8n-import` lo publica
en cada arranque. Verificalo con:

```bash
docker compose exec n8n n8n list:workflow
```

**Por qué hace falta ese paso extra.** `import:workflow` **siempre desactiva** lo que importa. El flag
`--activeState=fromJson` que serviría para respetar el JSON sólo funciona en modo queue o multi-main;
en una instancia normal falla con:

```
The "--activeState=fromJson" flag can only be used when n8n is running in queue or multi-main mode.
```

Sin compensarlo, **cada `docker compose up -d` apagaría el workflow y el cron no se dispararía nunca**,
en silencio. Por eso el servicio `n8n-import` corre, después de importar, un `publish:workflow` sobre
todo JSON que tenga `"active": true`. En los logs se ve la secuencia:

```
Deactivating workflow "Noticias IA diarias".
Successfully imported 3 workflows.
Activando noticias-ia-diarias
Publishing workflow with ID: noticias-ia-diarias (current version)
```

Para que un workflow arranque activo, poné `"active": true` en su JSON. Para desactivarlo, ponelo en
`false` (el interruptor de la UI también sirve, pero el próximo `up -d` lo pisa con lo que diga el JSON).

## Probarlo sin esperar a mañana

El workflow tiene un segundo trigger, **Probar ahora**, justo para eso:

- Desde la UI: abrí el workflow y click en **Execute workflow**.
- Desde la CLI:

```bash
docker compose exec -e N8N_RUNNERS_BROKER_PORT=5699 n8n n8n execute --id noticias-ia-diarias
```

El nodo *Seleccionar del día anterior* devuelve un campo `diagnostico` con el detalle del filtrado
(`{total, sinFecha, fueraDeRango, ok}`) — útil si un día llegan menos noticias de las esperadas.

## Cosas que conviene saber

- **Si el ordenador está apagado o suspendido a las 11:00, la ejecución no se recupera.** n8n no
  reintenta los triggers de schedule que se perdió. Para un digest que no se salte días necesitás
  que la máquina esté encendida, o mover esto a un VPS.
- **Ejecutarlo a media tarde da menos resultados que a las 11:00.** Los feeds sólo guardan entre 10 y
  20 noticias: por la tarde ya están llenos de las de hoy y las de ayer se cayeron de la ventana. Por
  eso las URLs de Google News se generan con `after:`/`before:` acotando el día anterior — sin eso,
  Google devuelve casi sólo noticias del día en curso.
- **La cabecera `anthropic-beta: server-side-fallback-2026-07-01`** junto con `fallbacks: 'default'`
  hace que, si un clasificador de seguridad rechaza la petición (puede pasar con noticias de
  ciberseguridad), Anthropic reintente automáticamente con otro modelo en la misma llamada. Si
  alguna vez te devuelve un 400 relacionado con esa beta, quitá la cabecera del nodo *Resumir con
  Claude* y el campo `fallbacks` del nodo *Seleccionar del día anterior*.
- **`new URL()` no existe dentro de los nodos Code de n8n.** El sandbox no expone todos los globales
  del navegador; por eso el host de cada noticia se saca con una expresión regular. Si añadís código,
  tenelo en cuenta.
- **`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`** en el compose es lo que permite que el nodo de Telegram lea
  `{{ $env.TELEGRAM_CHAT_ID }}`. Por defecto n8n bloquea el acceso a variables de entorno.
- **Editar el código de los nodos**: los nodos Code llevan JavaScript embebido dentro del JSON, que es
  incómodo de editar a mano por el escapado. Editalo en la UI de n8n y después exportá el workflow
  sobre `workflows/noticias-ia.json`.

## Añadir o quitar fuentes

Editá el nodo **Fuentes**. Cada entrada es `{ fuente, peso, url }`. Si el medio no tiene RSS,
probá con una búsqueda de Google News acotada a ese dominio:

```
https://news.google.com/rss/search?q=site:ejemplo.com+AI&hl=es&gl=ES&ceid=ES:es
```

---

## Comandos útiles

```bash
docker compose exec n8n n8n list:workflow   # listar workflows en la base
docker compose logs -f n8n          # ver logs
docker compose restart n8n          # reiniciar
docker compose down                 # bajar (los datos quedan en el volumen)
docker compose down -v              # bajar y BORRAR todo (workflows, credenciales, usuarios)
docker compose pull && docker compose up -d   # actualizar a la última imagen
```

## Cómo funciona la importación

El servicio `n8n-import` corre una sola vez (`n8n import:workflow --separate --input=/workflows`)
sobre el mismo volumen que usa la instancia principal, y recién cuando termina bien arranca `n8n`.

- Al reejecutar `docker compose up`, vuelve a importar: los JSON de `workflows/` **pisan**
  lo que haya en la base con el mismo `id`. Si editás un workflow desde la UI, exportalo a
  `workflows/` antes de reiniciar o vas a perder los cambios.
- Para reimportar sin tocar el resto: `docker compose up -d --force-recreate n8n-import`
- Los workflows se importan **inactivos** y las **credenciales no se tocan** (viven en la base, no
  en los JSON), así que reimportar no te obliga a reconfigurar Anthropic ni Telegram.

## Notas

- Probado con **n8n 2.33.7** (`:latest` al 2026-08-10). En n8n 2.x los workflows tienen versión
  *borrador* y *publicada*; el importado queda como borrador sin publicar.
- La persistencia usa **SQLite** dentro del volumen `n8n_data` (suficiente para un MVP).
  Para algo más serio conviene agregar un servicio Postgres y setear `DB_TYPE=postgresdb`.
- `N8N_ENCRYPTION_KEY` cifra las credenciales guardadas. Si la cambiás, las credenciales
  existentes dejan de poder desencriptarse.
- `N8N_SECURE_COOKIE=false` está sólo para desarrollo sobre HTTP en localhost. Si lo exponés
  a internet, poné HTTPS por delante y sacá esa variable.
