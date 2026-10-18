
# Archivo:
# D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\acciones\registrar_fallo_asiento.py

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("chaskitambo.plugin.sunarp")

BASE_DIR = Path(__file__).resolve().parents[3]
DIRECTORIO_REVISION = BASE_DIR / "revisar_manualmente"
ARCHIVO_INCIDENCIAS = DIRECTORIO_REVISION / "incidencias_asientos.json"


def registrar_fallo_asiento(
    partida_registral: str,
    asiento: str,
    pagina: str,
    motivo: str,
    expediente: str | None = None,
) -> bool:
    """Registra una incidencia por partida, asiento y página.

    Devuelve True si se creó una incidencia nueva y False si ya existía.
    Los errores de persistencia se propagan al llamador.
    """
    partida = str(partida_registral or "").strip()
    numero_asiento = str(asiento or "").strip()
    numero_pagina = str(pagina or "").strip()
    detalle = str(motivo or "").strip()

    if not partida or not numero_asiento or not numero_pagina or not detalle:
        raise ValueError(
            "Partida, asiento, página y motivo son obligatorios."
        )

    DIRECTORIO_REVISION.mkdir(parents=True, exist_ok=True)

    if ARCHIVO_INCIDENCIAS.exists():
        with ARCHIVO_INCIDENCIAS.open("r", encoding="utf-8") as archivo:
            datos: Any = json.load(archivo)

        if not isinstance(datos, dict) or not isinstance(
            datos.get("incidencias"), list
        ):
            raise ValueError(
                f"Formato inválido en {ARCHIVO_INCIDENCIAS}; "
                "se esperaba un objeto con una lista 'incidencias'."
            )
    else:
        datos = {"incidencias": []}

    incidencias = datos["incidencias"]

    for registro in incidencias:
        if not isinstance(registro, dict):
            continue

        misma_clave = (
            str(registro.get("partida_registral", "")).casefold()
            == partida.casefold()
            and str(registro.get("asiento", "")).casefold()
            == numero_asiento.casefold()
            and str(registro.get("pagina", "")).casefold()
            == numero_pagina.casefold()
        )

        if misma_clave:
            registro["ultima_fecha"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            registro["ultimo_motivo"] = detalle
            registro["intentos"] = int(registro.get("intentos", 1)) + 1
            registro["requiere_revision_manual"] = True
            _guardar(datos)
            logger.warning(
                "Incidencia actualizada: partida=%s, asiento=%s, página=%s",
                partida,
                numero_asiento,
                numero_pagina,
            )
            return False

    ahora = datetime.now().astimezone().isoformat(timespec="seconds")

    incidencias.append(
        {
            "partida_registral": partida,
            "expediente": (
                str(expediente).strip()
                if expediente is not None and str(expediente).strip()
                else None
            ),
            "asiento": numero_asiento,
            "pagina": numero_pagina,
            "estado": "error",
            "motivo": detalle,
            "fecha_registro": ahora,
            "ultima_fecha": ahora,
            "intentos": 1,
            "requiere_revision_manual": True,
        }
    )

    _guardar(datos)

    logger.warning(
        "Incidencia registrada: partida=%s, asiento=%s, página=%s. Archivo=%s",
        partida,
        numero_asiento,
        numero_pagina,
        ARCHIVO_INCIDENCIAS,
    )
    return True


def _guardar(datos: dict[str, Any]) -> None:
    """Escritura atómica del registro de incidencias."""
    archivo_temporal = ARCHIVO_INCIDENCIAS.with_suffix(".json.tmp")

    try:
        with archivo_temporal.open("w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())

        archivo_temporal.replace(ARCHIVO_INCIDENCIAS)
    finally:
        if archivo_temporal.exists():
            archivo_temporal.unlink()
