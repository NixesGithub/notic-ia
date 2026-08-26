# notic-ia

**Estado:** funcionando
**Stack:** Python (sólo `feedparser` fuera de la librería estándar), GitHub Actions, API de Anthropic, Telegram, n8n en Docker (versión local)

## Qué es

El digest diario que manda esto: noticias de IA del día anterior, repos de
GitHub en tendencia y un resumen final de lo que cambia algo de verdad. Sale a
las 9:00 hora de Madrid desde los runners de GitHub, sin depender de ninguna
máquina propia.

## Bloqueos

- GitHub no publica ninguna API de trending, así que los repos salen de raspar
  el HTML de `github.com/trending`. Se rompe cuando cambian el marcado — ya
  pasó una vez con el icono dentro del enlace de estrellas.
- No hay histórico de estrellas accesible: sólo se sabe cuántas ganó un repo
  hoy, no cómo viene evolucionando.
- El mismo pipeline existe dos veces, en Python y en nodos de n8n, y hay que
  cambiar las dos a mano o se van a la deriva.
- No hay memoria entre ejecuciones: si una noticia sale tres días seguidos, el
  digest la trata como nueva cada vez.
- No hay forma de decirle "esto no me interesó" y que lo tenga en cuenta
  mañana; ajustar el filtro es editar el prompt a mano.
- Las fuentes son 9 feeds fijos elegidos a dedo; no hay manera de descubrir
  fuentes nuevas ni de saber cuáles aportan y cuáles sólo hacen ruido.

## Ideas

- Un resumen semanal que junte los siete digests y saque la tendencia de fondo.
- Poder responderle al bot de Telegram para ajustar el criterio sin tocar código.
- Que el propio digest detecte cuándo se rompió el parseo y abra un issue solo.
- Meter fuentes en audio (podcasts) transcribiéndolas.
