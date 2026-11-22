# tsp-registro

Registro central de The Stromboli Project (PAA2, fase 2, tarjeta S-1264). **En observación: aún no
sustituye nada** (ni el tablero ni las carpetas de tarea de Lion).

Diseño: `2606-thestromboliproject/docs/organizacion/02-arquitectura.md` §3, §3.1 bis, §3.1 ter,
§3.3 y §9 bis.

## Cómo funciona

- **Un fichero por evento** en `eventos/AAAA/MM/DD/AAAAMMDDTHHMMSSZ-<id>.json`. Solo se añade; un
  evento nunca se edita ni se borra.
- **`org` es el único escritor.** Valida cabecera, versión de esquema, papel del autor y transición
  antes de escribir. Un fichero que no pase (por ejemplo, escrito a mano) queda en `rechazados` y no
  cuenta para el estado.
- **Estado calculado** en `estado/` (un JSON por tipo de entidad, `rechazados.json`, `resumen.json`)
  e **índice SQLite** en `.indice/`. Los dos son desechables y están fuera de git: se borran y se
  recalculan desde `eventos/` (`org recalcular`).
- **Sin dependencias:** Python 3.10+ y biblioteca estándar.

## Uso

```
py -m org tipos [--papel trabajador]          # qué eventos hay y quién puede escribirlos
py -m org escribir tarea.propuesta tarea:T-0001 --clave T-0001:propuesta --papel direccion --identidad claude-S-1264 --datos "{\"titulo\": \"...\", \"repo\": \"...\"}"
py -m org ver tarea:T-0001                    # estado calculado de una entidad
py -m org estado                              # pone al día el índice y escribe estado/
py -m org recalcular                          # borra índice y estado/ y los recalcula
py -m org rechazados                          # eventos en disco que no pasan
py -m org subir                               # commit de los eventos nuevos y push
```

- `--datos` admite JSON, `@fichero.json` o `-` (entrada estándar).
- Papel e identidad: `--papel`/`--identidad` o `ORG_PAPEL`/`ORG_IDENTIDAD`. Raíz: `--raiz` u
  `ORG_RAIZ` (por defecto, este repo).
- Salida: 0 aceptado (o repetido sin cambios: `"nuevo": false`), 1 rechazado, 2 error.

## Cabecera de un evento

`id` (16 hex) · `tipo` · `version_esquema` · `fecha` (UTC, microsegundos) · `autor {papel,
identidad}` · `entidad {tipo, id}` · `correlacion` (id del evento que lo originó, o null) ·
`clave_idempotencia` · `datos`.

- **Idempotencia:** la misma clave con el mismo contenido no hace nada; con otro contenido se
  rechaza. Si dos máquinas usan la misma clave sin verse, al juntarse cuenta la primera.
- **Orden:** por `(fecha, id)`. `org` adelanta la fecha a la del último evento de esa entidad (y a
  la del correlacionado), así que un reloj atrasado no desordena. Si un `pull` trae eventos
  anteriores a los ya indexados, el índice se rehace solo.

## Papeles y transiciones

`py -m org tipos` da la tabla completa. Lo esencial (02 §3.1 bis):

| Papel | Puede escribir |
|---|---|
| trabajador | latido, punto de control, entrega, pausa, bloqueo, decisión A/B, incidencia |
| validador / laboratorio | evidencia verificada, verificada, devuelta, integrada (y cerrada, el validador) |
| planificador | aprobada (M6), lista, asignada, caducada, pausa, bloqueo |
| direccion / motor | propuesta de tarea (dirección también descarta); el motor, incidencias |
| javier | objetivo, propuesta, aprobada, descartada, pausa, cerrada, decisión C |
| fabrica | nada aún (ficha de release: P6-1) |

Tarea: `propuesta → aprobada → lista → en_curso → entregada → verificada → integrada → cerrada`, con
`en_pausa`, `bloqueada`, `caducada` y `devuelta` volviendo a `lista`, y `descartada`.

## Rechazados: esquema y conflicto

- **esquema:** el fichero está mal (JSON ilegible, cabecera, tipo, versión, papel, datos, nombre
  que no corresponde). No cuenta y `org subir` se niega a subirlo.
- **conflicto:** el evento es correcto pero no encaja: dos máquinas movieron la misma tarea sin
  verse y, al juntarse, el que queda detrás ya no vale (o repite clave). No cuenta, se sube igual
  y todas las máquinas lo ven en `estado/rechazados.json`. Resolverlo es escribir un evento nuevo.
- Si un fichero ya indexado cambia o desaparece, el índice se rehace solo; vigilar que nadie toque
  la historia es cosa de git (`org subir` avisa).

## Decisiones tomadas (S-1264, revisables)

| N.º | Decisión | Descartada | Por qué |
|---|---|---|---|
| D-1 | `estado/` y `.indice/` fuera de git | Commitear `estado/` | Dos escritores tocarían el mismo fichero (02 §3.1) |
| D-2 | Un tipo de evento por transición (`tarea.verificada`…) | Un genérico «cambio de estado» | El papel se comprueba por tipo (02 §3.1 bis) |
| D-3 | `--clave` obligatoria | Deducirla del contenido | Dos latidos iguales chocarían |
| D-4 | `tarea.cerrada` la escriben validador y Javier | Solo Javier | 02 no dice quién; cerrar tras integrar es automático |
| D-5 | `en_pausa`, `bloqueada`, `caducada` y `devuelta` vuelven por `tarea.lista` | Un `tarea.reanudada` | Menos tipos; el planificador decide cuándo sigue |
| D-6 | Además del diagrama de 02 §3.3: devuelta desde verificada (02 §8.4), bloqueada desde entregada por el validador (02 §9 ter, devoluciones antes de bloquear), descartada desde lista | Ceñirse al diagrama | Casos que el propio 02 describe en otras secciones |
| D-7 | Conflictos se suben; mal formados no | Bloquear toda subida con rechazados | Un conflicto no se puede «arreglar» sin borrar historia |

Pendiente de llevar a `02-arquitectura.md` cuando se toque (lo lleva la sesión que lo edite).

## Límites conocidos

- **La identidad es declarada.** En el PC todas las sesiones son el usuario de Javier: `org`
  comprueba el papel que se declara, no quién es de verdad. La separación fuerte (credencial por
  papel, rama principal protegida y paso de entrada que revalida cada evento) es de la fase 4.
- Cada escritura recorre `eventos/` para ver si hay ficheros nuevos; con cientos de miles de
  eventos habrá que cambiarlo.

## Tests

`py -m unittest discover -s tests -v`
