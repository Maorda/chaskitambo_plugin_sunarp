
# Archivo: src/chaskitambo_plugin_sunarp/acciones/extraer_partida.py

import nodriver as uc

from .oficina import seleccionar_oficina
from .area import seleccionar_area
from .criterio import configurar_criterio_partida
from .buscar import ejecutar_busqueda_y_esperar_spinner
from .verificador import verificar_estado_y_previsualizar


async def extraer_partida(
    page: uc.Tab,
    oficina: str,
    area: str,
    partida: str,
) -> list[dict] | None:
    print("\n=========================================")
    print(f"PROCESANDO PARTIDA: {partida} en {oficina}")
    print("=========================================")

    try:
        await seleccionar_oficina(page, oficina)
        await seleccionar_area(page, area)
        await configurar_criterio_partida(page, partida)
        await ejecutar_busqueda_y_esperar_spinner(page)

        return await verificar_estado_y_previsualizar(page)

    except Exception as exc:
        print(f"[-] Error crítico en la orquestación: {exc}")
        await page.save_screenshot("error_critico_orquestador.png")
        return None
