
import logging
import random

import nodriver as uc


logger = logging.getLogger("chaskitambo.plugin.sunarp")


async def seleccionar_area(page: uc.Tab, area: str) -> bool:
    """
    Selecciona el área registral en SUNARP.

    Devuelve:
        True: se encontró y pulsó una opción.
        False: no se pudo completar la selección.
    """
    if not isinstance(area, str) or not area.strip():
        logger.error("El nombre del área registral está vacío o no es válido.")
        return False

    area = area.strip()

    logger.info("[Acción] Seleccionando área registral: %s", area)

    try:
        selectores = await page.select_all(
            "nz-select-top-control.ant-select-selector"
        )

        if len(selectores) < 2:
            logger.error(
                "No se encontró el segundo selector para el área registral."
            )
            return False

        # 1. Abrir el desplegable del área registral.
        await selectores[1].click()
        await page.sleep(random.uniform(0.5, 0.8))

        # 2. Localizar el campo de búsqueda activo.
        inputs = await page.select_all(
            "nz-select-search input.ant-select-selection-search-input"
        )

        if not inputs:
            logger.error(
                "No se encontró el campo de búsqueda del área registral."
            )
            return False

        input_search = inputs[-1]
        await input_search.send_keys(area)
        await page.sleep(random.uniform(1.0, 1.5))

        # 3. Seleccionar la primera opción disponible.
        opciones = await page.select_all(".ant-select-item-option")

        if not opciones:
            logger.error(
                "No se encontraron opciones para el área registral: %s",
                area,
            )
            return False

        await opciones[0].click()
        await page.sleep(random.uniform(0.6, 1.0))

        logger.info(
            "Se hizo clic en una opción para el área registral: %s",
            area,
        )
        return True

    except Exception:
        logger.exception(
            "Error al seleccionar el área registral: %s",
            area,
        )
        return False
