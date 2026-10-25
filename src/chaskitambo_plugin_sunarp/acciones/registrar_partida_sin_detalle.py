from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("chaskitambo.plugin.sunarp")

# Raíz del repositorio del plugin:
# D:\libs\chaskitambo_plugin_sunarp
BASE_DIR = Path(__file__).resolve().parents[3]

DIRECTORIO_REVISION = BASE_DIR / "revisar_manualmente"
ARCHIVO_PARTIDAS = DIRECTORIO_REVISION / "partidas_sin_detalle.json"


async def registrar_partida_sin_detalle(
    partida_registral: str,
    expediente: str | None = None,
) -> bool:
    """
    Registra una partida cuya búsqueda SUNARP terminó sin resultados.

    Retorna:
        True: se añadió una nueva partida.
        False: la partida ya estaba registrada.

    Los errores de lectura o escritura del JSON se propagan
    para que el coordinador pueda registrarlos en el log.
    """
    partida = str(partida_registral or "").strip()

    if not partida:
        raise ValueError(
            "No se puede registrar una partida registral vacía."
        )

    DIRECTORIO_REVISION.mkdir(parents=True, exist_ok=True)

    if ARCHIVO_PARTIDAS.exists():
        with ARCHIVO_PARTIDAS.open("r", encoding="utf-8") as archivo:
            datos: Any = json.load(archivo)

        if not isinstance(datos, dict):
            raise ValueError(
                f"El archivo {ARCHIVO_PARTIDAS} debe contener un objeto JSON."
            )

        partidas = datos.get("partidas")

        if not isinstance(partidas, list):
            raise ValueError(
                f"El archivo {ARCHIVO_PARTIDAS} debe contener "
                "una lista llamada 'partidas'."
            )
    else:
        datos = {"partidas": []}
        partidas = datos["partidas"]

    # No duplicar partidas que ya estén registradas.
    for registro in partidas:
        if not isinstance(registro, dict):
            continue

        existente = str(
            registro.get("partida_registral", "")
        ).strip()

        if existente.casefold() == partida.casefold():
            logger.info(
                "La partida %s ya figura en %s. "
                "No se añade un duplicado.",
                partida,
                ARCHIVO_PARTIDAS,
            )
            return False

    nuevo_registro = {
        "partida_registral": partida,
        "estado": "sin_registros_en_busqueda",
        "motivo_observado": (
            "SUNARP mostró Total de registros encontrados: 0"
        ),
        "expediente": (
            str(expediente).strip()
            if expediente is not None and str(expediente).strip()
            else None
        ),
        "fecha_registro": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "requiere_revision_manual": True,
    }

    partidas.append(nuevo_registro)
    datos["partidas"] = partidas

    # Escritura temporal para evitar dejar el JSON incompleto
    # si el proceso se interrumpe durante la escritura.
    archivo_temporal = ARCHIVO_PARTIDAS.with_suffix(".json.tmp")

    try:
        with archivo_temporal.open("w", encoding="utf-8") as archivo:
            json.dump(
                datos,
                archivo,
                ensure_ascii=False,
                indent=2,
            )
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())

        archivo_temporal.replace(ARCHIVO_PARTIDAS)

    finally:
        if archivo_temporal.exists():
            archivo_temporal.unlink()

    logger.warning(
        "Partida %s registrada para revisión manual: %s",
        partida,
        ARCHIVO_PARTIDAS,
    )

    return True
