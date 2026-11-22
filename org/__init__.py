"""org: programa único que escribe el registro central de The Stromboli Project (PAA2, S-1264)."""
from .esquemas import PAPELES, TIPOS, Conflicto, Rechazo
from .registro import Registro, Resultado

__all__ = ["PAPELES", "TIPOS", "Conflicto", "Rechazo", "Registro", "Resultado"]
