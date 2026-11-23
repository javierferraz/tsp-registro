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

## Importador y comparación diaria (S-1265)

El registro copia lo que dicen las fuentes de hoy, **sin cambiarlas ni sustituirlas**: el tablero
sigue mandando.

```
py -m org diario                  # importar + comparar + cartera; deja informes/AAAA-MM-DD.md y .json y sube
py -m org importar                # solo copiar en el registro lo que ha cambiado
py -m org comparar [--md] [--desde AAAA-MM-DD]   # registro frente a tablero, sin escribir nada
py -m org cartera                 # cartera desde el manifest frente a las cuatro listas
```

- **Fuentes, todas solo lectura:** `tablero/board.json` del disco del repo central (es el fichero
  vivo); las carpetas de tarea de Roaring Lion (`%LOCALAPPDATA%\RoaringLion\tasks`); y, desde
  `origin/main` del repo central tras un `git fetch`, los informes de los vigías, el manifest y las
  cuatro listas (la copia local puede ir atrasada, 01 a4). El `fetch` solo pone al día las
  referencias remotas del central: no toca su carpeta de trabajo ni sus ramas. Opciones: `--central`, `--lion`, `--ref`,
  `--sin-fetch`, `--sin-subir`, `--fecha`.
- **Qué compara:** fidelidad (registro ↔ tablero y Lion, campo a campo), anomalías de la historia
  (saltos fuera del ciclo de 02 §3.3, revisiones que bajan, cambios de columna sin nota, tarjetas
  que desaparecen), tareas de Lion que no casan con la columna de su tarjeta y vigías sin informe
  nuevo a tiempo. Más la cartera del manifest frente a `catalog.js`, `catalogo-apps.json`,
  `app-matrix.md` y `CONTEXT.md` §8.
- **Tarea programada** `TSP registro diario` (cada día a las 07:15, y al encender si se pasó):
  `wscript.exe ejecutar-oculto.vbs py -m org diario`, sin ventana. Registro de pasadas en
  `.indice/diario.log`.
- **Privacidad:** nunca entran títulos, cuerpos, notas, prompts ni correos. De una tarjeta, solo
  columna, fechas, revisión, etiquetas, app, número de notas, commits y una huella del texto (16
  caracteres de un sha256) que dice *que* cambió, no *qué*. Del soporte, solo cuántos correos.

## El parte diario (S-1266, PAA2 P3-1)

Una sola página HTML generada del registro: `estado/parte.html` (fuera de git; se rehace cada
mañana al final de `org diario`, o a mano con `py -m org parte`). Se abre en Helios. **Corre al
lado del parte de Telegram, que no cambia**, hasta que Javier prefiera uno de los dos.

- **Orden (06 §5.1):** lo que le toca a Javier arriba (preguntas en «Pendiente», tarjetas de «Por
  validar» con comprobación *decisión*, tareas de Lion paradas), y luego problemas, hecho en las
  últimas 24 h, en marcha, sin verificar, negocio y gasto.
- **Dice lo que no sabe:** registro sin informe de hoy, vigía con «sin cambios» falso, muestra
  pequeña, ventas y coste sin dato. Si una sección está vacía, lo dice en una línea.
- **Tope:** 5 filas por sección y 8 identificadores por fila agrupada; el resto se cuenta.
- **Sin títulos ni rutas:** el registro no los guarda (D-8). Cada tarjeta enlaza al tablero
  local, donde está el detalle; las tareas de Lion salen con su id y su repo, sin enlace.
- Solo lee: no escribe eventos. Si falla, `org diario` sigue y lo apunta en su línea del log.

| N.º | Decisión (S-1266) | Descartada | Por qué |
|---|---|---|---|
| D-17 | Sin títulos de tarjeta: id con enlace al tablero, app y antigüedad | Leer los títulos del tablero al generar | Pueden llevar nombres o textos de clientes (D-8 y prompt P3-1: sin datos personales) |
| D-18 | «Por validar» con *ojos* o *máquina* va a «Sin verificar», no arriba | Todo «Por validar» arriba | Esas las puede cerrar otro verificador (PAA M5); arriba solo lo que solo decide Javier |
| D-19 | Ventana fija de 24 h | «Desde tu última visita» | El parte no sabe cuándo lo abrió Javier; queda para la puerta (P3-2) |
| D-20 | `estado/parte.html`, fuera de git | `informes/` en git | Se regenera entero cada día; en git solo sumaría ruido |

## Contrato de trabajo ampliado y reserva de recursos (S-1269, PAA2 P4-1)

Diseño: `02-arquitectura.md` §4, §5.1, §8.3 y §9 ter. **Aún no sustituye al tablero** ni a las
carpetas de tarea de Lion: son las piezas que usarán el planificador (P5-1) y el validador (P4-2).

```
py -m org latido T-1 --identidad w1 [--ficheros a.py b.py]     # agrupado; sube si escribe
py -m org punto-control T-1 --identidad w1 --hecho "…" --sigue "…" [--ficheros …] [--a-medias …] [--rama s1] [--commit abc]
py -m org caducar --identidad plan [--minutos 20]               # el planificador
py -m org contrato T-1                                          # lo que recibe el trabajador
py -m org reservar manifest --identidad w1 [--minutos 30] [--tarea T-1]   # 0 = tuya, 3 = no
py -m org liberar R-… --identidad w1  ·  py -m org renovar R-… --identidad w1 --minutos 30
py -m org recursos  ·  py -m org volumen
```

- **Latido agrupado.** Se puede latir tan a menudo como se quiera: solo se escribe un evento si el
  último de la tarea tiene 10 min o más (12 si el día ya pasa de 500 eventos) y, al escribirse,
  se sube con todo lo pendiente. Entre medias, los ficheros tocados se acumulan en el índice local
  y salen en el siguiente latido o punto de control. Medido en los tests: 7 tareas latiendo cada
  5 min durante 14 h dejan 546 latidos (sin agrupar serían 1.176).
- **Punto de control** (v2): qué está hecho, qué sigue, ficheros tocados y a medias, rama y
  commit. Se escribe y se sube siempre.
- **Relevo.** Sin latido en 20 min, `caducar` la marca `caducada`; el planificador la devuelve a
  `lista` y la asigna a otro. `contrato` le da el último punto de control, la rama, los ficheros
  tocados y sus reservas. La ejecución anterior queda en `ejecuciones_anteriores`.
- **Informe de entrega** (`tarea.entregada` v2), sin campos libres: criterios con estado `ok`
  (con evidencia), `falla` o `no_ejecutado` (con motivo), ficheros, commits, decisiones con el
  mismo esquema que `decision.tomada` (sin clase C), lo descubierto de paso y el coste.
  `contrato` lo describe en `informe_v2`.
- **Reservas.** Una entidad `reserva` (R-…) por petición, con plazo (30 min por defecto, hasta 24 h)
  que caduca solo; se renueva mientras está viva y una caducada no se resucita. **Gana la que
  entró antes en la historia compartida**: se pide, se sube y se vuelve a leer; si hay otra viva
  delante, se escribe la retirada. Si el índice ya dice que otro la tiene, sale «ocupado» sin
  escribir nada.

| N.º | Decisión (S-1269) | Descartada | Por qué |
|---|---|---|---|
| D-21 | «La más antigua» es la que entró antes en la historia compartida (orden de los commits tras subir); la fecha solo desempata | Comparar la `fecha` del evento | Con la fecha, quien sube primero con un reloj adelantado o con su petición más tarde se cree solo y empieza; la otra llega después con fecha anterior y también gana. El test `test_gana_la_que_entra_antes…` falla con ese criterio |
| D-22 | Una entidad `reserva` por petición, con el recurso en los datos | Una entidad por recurso | El estado calculado ordena por fecha: decidiría el ganador al revés que la historia (D-21) |
| D-23 | Caducidad por plazo propio de la reserva, con `renovar` | Atarla al latido de la tarea | `ftp`, `firma` o `manifest` también se piden fuera de una tarea; renovar es raro y cuesta un evento |
| D-24 | Latido como evento cada 10 min por tarea (12 con el día por encima de 500) y subida al escribirse; el trabajador late cada 5 min o menos (`contrato` lo dice en `latir_cada_min`) | Un evento por latido y subida cada 10 min; 15 min saturado | Un latido por minuto de 5 trabajadores serían 7.200 eventos al día. Con 15 min y un trabajador que late cada 5, el latido escrito podía caer en el minuto 20, el de la caducidad (revisión en frío); con 12, como mucho en el 17. **El tope es blando**: un latido que no sale hace caducar la tarea, que es peor que pasarse; pasarse es la señal de 02 §3.1 ter (`org volumen`) |
| D-25 | v2 de latido, punto de control y entrega; la v1 sigue admitida | Ampliar la v1 | `CLAUDE.md`: nunca se cambia el significado de una versión publicada |
| D-26 | En v2, latido, punto de control y entrega solo valen del ejecutor asignado | Cualquier trabajador | Uno que vuelve tras caducar no puede pisar el relevo (02 §8.3: una sola ejecución viva) |
| D-27 | El informe usa el esquema de `decision.tomada` para sus decisiones, sin clase C | Un esquema propio | 02 §4: un solo esquema de decisiones; la C solo la escribe Javier |

Límites: la caducidad compara el plazo con el reloj de cada máquina (un desfase de minutos puede
dar por viva o caducada una reserva en el borde, y dos máquinas creerse titulares durante ese
desfase); `caducar` decide con lo que ha llegado al índice, así que un latido que no se pudo
subir no cuenta; la protección del relevo (D-26) solo cubre los eventos v2 (los trabajadores
nuevos usan v2, la v1 queda para lo ya escrito); sin red, `reservar` no confirma y se retira
(«sin_confirmar»); las reservas por máquina y pantalla dependen de que todos los procesos usen
`org`, no hay nada que impida usar el recurso sin reservarlo.

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
| trabajador | latido, punto de control, entrega, pausa, bloqueo, decisión A/B, incidencia; pedir, renovar, liberar y retirar su reserva |
| validador / laboratorio | evidencia verificada, verificada, devuelta, integrada (y cerrada, el validador) |
| planificador | aprobada (M6), lista, asignada, caducada, pausa, bloqueo; reservas (también soltar la de otro) |
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
| D-8 | Del tablero y de Lion solo estructura, fechas, cifras y una huella del texto | Copiar títulos | Pueden llevar nombres o textos de clientes (prompt P2-2) |
| D-9 | Papel nuevo `importador`, que solo escribe tipos espejo (`tarjeta.*`, `ejecucion.*`, `informe.*`) | Importar como `tarea.*` | El ciclo de tareas lo escribirá la organización nueva; mezclarlo falsearía la comparación |
| D-10 | El estado del espejo es la columna (o el estado de Lion) tal cual, y cualquier cambio vale | Validar el ciclo al importar | El tablero manda: un salto raro se informa como anomalía, no se rechaza |
| D-11 | Primera pasada: una foto por tarjeta con todas sus notas de transición dentro; después, solo los movimientos nuevos | Un evento por nota histórica | 9.000 eventos de golpe frente a 1.328; la historia queda igual de completa |
| D-12 | Columnas del tablero a estados de 02 §3.3: Backlog y Propuestas → propuesta, Empezar → aprobada, En curso → en_curso, Pendiente → bloqueada, Por validar → entregada, Terminada → cerrada | Mapa más fino | El tablero no distingue lista, verificada ni integrada |
| D-13 | Los informes diarios van a `informes/` (en git) | Dejarlos en `estado/` | `recalcular` borra `estado/` |
| D-14 | Cadencia esperada de los vigías: soporte 2 días, releases 5, contenidos, fichas y tablero 8 | Un plazo único | Cada vigía corre con su propio ritmo (diario, lunes y jueves, semanal) |
| D-15 | Lista blanca por patrón: un campo de texto solo entra si parece un identificador corto (estado, app, etiqueta, carpeta, rama); el autor de un movimiento, solo `claude`, `javier` u `otro` | Confiar en que hoy los valores son benignos | Una rama de Lion ya llevaba prosa del agente (revisión en frío) |
| D-16 | Propuestas → En curso sin Empezar se cuenta aparte como ruido conocido | Listarlo con las demás anomalías | PAA M6 y los prompts lanzados por Javier lo hacen legítimo; tapaba lo demás (270 de 426) |

Pendiente de llevar a `02-arquitectura.md` cuando se toque (lo lleva la sesión que lo edite).

## Límites conocidos

- **La identidad es declarada.** En el PC todas las sesiones son el usuario de Javier: `org`
  comprueba el papel que se declara, no quién es de verdad. La separación fuerte (credencial por
  papel, rama principal protegida y paso de entrada que revalida cada evento) es de la fase 4.
- Cada escritura recorre `eventos/` para ver si hay ficheros nuevos; con cientos de miles de
  eventos habrá que cambiarlo. El importador escribe en lote (`escribir_lote`): un recorrido por
  pasada.
- **Lectores de las cuatro listas por patrón de texto.** Si cambia el formato de una tabla, salen
  hallazgos falsos o la lista sale vacía; en `CONTEXT.md` §8 la «versión» es la primera que aparece
  en la fila, que puede ser una antigua citada.
- **Coste por ejecución:** Lion no guarda coste ni tokens en sus carpetas de tarea; solo se apuntan
  los minutos entre creación y última actualización.
- **Enlace Lion ↔ tablero débil:** pocas tareas de Lion llevan su tarjeta.

## Tests

`py -m unittest discover -s tests -v`
