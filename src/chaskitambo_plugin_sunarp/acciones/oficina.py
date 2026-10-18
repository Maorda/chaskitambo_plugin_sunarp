# Archivo: acciones/pre_extraer_partida/oficina.py

import random
import nodriver as uc


async def seleccionar_oficina(page: uc.Tab, oficina: str) -> bool:
    print(f"[Acción] Seleccionando oficina: {oficina}...")

    # 1. Desplegar el selector de oficina.
    selectores = await page.select_all(
        "nz-select-top-control.ant-select-selector"
    )

    if not selectores:
        print("[-] No se encontró el selector de oficina.")
        return False

    await selectores[0].click()
    await page.sleep(random.uniform(0.5, 0.8))

    # 2. Escribir el nombre de la oficina.
    inputs = await page.select_all(
        "nz-select-search input.ant-select-selection-search-input"
    )

    if not inputs:
        print("[-] No se encontró el campo de búsqueda de oficina.")
        return False

    await inputs[0].send_keys(oficina)
    await page.sleep(random.uniform(1.0, 1.5))

    # 3. Seleccionar la opción renderizada.
    opciones = await page.select_all(".ant-select-item-option")

    if not opciones:
        print(f"[-] No se encontró una opción para: {oficina}")
        return False

    await opciones[0].click()
    await page.sleep(random.uniform(0.6, 1.0))

    print(f"[Acción] Se hizo clic en la opción para: {oficina}")
    return True