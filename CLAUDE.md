# CLAUDE.md — tsp-registro

Registro central de The Stromboli Project (PAA2). Lee `README.md` antes de tocar nada.

- **Nunca escribas ni edites a mano un fichero de `eventos/`.** Todo evento entra con
  `py -m org escribir …`; `org subir` se niega a subir eventos modificados o rechazados.
- **Código (`org/`, `tests/`):** semáforo antes de cada escritura
  (`py C:/xampp/htdocs/2606-thestromboliproject/scripts/sema.py tomar …`) y tests en verde
  (`py -m unittest discover -s tests`) antes del commit.
- Un cambio de esquema añade una versión nueva del tipo; se admiten dos a la vez y la vieja se
  retira con aviso (02 §3.1 ter). Nunca se cambia el significado de una versión publicada.
- **«verificada» solo la escribe `org validar`** (papel validador, con sello cuando está
  activo). Un trabajador nunca escribe con papel validador, laboratorio ni javier, ni toca
  `politica/firmantes` (S-1270).
- Ni credenciales ni `.env` en el repo.
