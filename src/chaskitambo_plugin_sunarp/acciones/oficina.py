
# Archivo: acciones/oficina.py

import random
import re
import unicodedata

import nodriver as uc


def _normalizar_oficina(valor: str) -> str:
    """Normaliza el texto para comparar nombres de oficinas."""
    texto = unicodedata.normalize("NFKD", valor or "")
    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )
    return re.sub(r"\s+", " ", texto).strip().casefold()


async def seleccionar_oficina(page: uc.Tab, oficina: str) -> bool:
    """Selecciona exclusivamente la oficina registral solicitada."""
    oficina_solicitada = _normalizar_oficina(oficina)

    if not oficina_solicitada:
        print("[-] La oficina registral solicitada está vacía.")
        return False

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

    # 2. Buscar la oficina solicitada.
    inputs = await page.select_all(
        "nz-select-search input.ant-select-selection-search-input"
    )

    if not inputs:
        print("[-] No se encontró el campo de búsqueda de oficina.")
        return False

    await inputs[0].send_keys(oficina)
    await page.sleep(random.uniform(1.0, 1.5))

    # 3. Obtener los textos de las opciones directamente del DOM.
    datos_opciones = await page.evaluate(
        """
        (() => Array.from(
            document.querySelectorAll(".ant-select-item-option")
        ).map((elemento) => ({
            title: elemento.getAttribute("title") || "",
            text: elemento.innerText || elemento.textContent || ""
        })))()
        """
    )

    opciones = await page.select_all(".ant-select-item-option")

    print(f"[DIAGNÓSTICO] Oficina solicitada: {oficina!r}")
    print(f"[DIAGNÓSTICO] Opciones Nodriver: {len(opciones)}")
    print(
        f"[DIAGNÓSTICO] Tipo de datos DOM: "
        f"{type(datos_opciones).__name__}"
    )
    print(f"[DIAGNÓSTICO] Datos DOM: {datos_opciones!r}")

    print(f"[DIAGNÓSTICO] Oficina solicitada: {oficina!r}", flush=True)
    print(f"[DIAGNÓSTICO] Opciones Nodriver: {len(opciones)}", flush=True)
    print(
        f"[DIAGNÓSTICO] Tipo DOM: {type(datos_opciones).__name__}",
        flush=True,
    )
    print(
        f"[DIAGNÓSTICO] Datos DOM: {datos_opciones!r}",
        flush=True,
    )

    if not opciones:
        print(
            f"[-] No existe ninguna opción para la oficina: "
            f"{oficina!r}"
        )
        return False

    if not isinstance(datos_opciones, list):
        print("[-] No se pudieron leer los textos de las opciones.")
        return False

    # 4. Seleccionar únicamente una coincidencia exacta.
    for indice, opcion in enumerate(opciones):
        if indice >= len(datos_opciones):
            break

        datos = datos_opciones[indice]

        # Nodriver puede devolver objetos JavaScript serializados
        # como {"type": "object", "value": [[clave, valor], ...]}.
        if isinstance(datos, dict) and datos.get("type") == "object":
            pares = datos.get("value", [])

            if not isinstance(pares, list):
                continue

            datos_normalizados = {}

            for par in pares:
                if not isinstance(par, (list, tuple)) or len(par) != 2:
                    continue

                clave, valor = par

                if isinstance(valor, dict) and valor.get("type") == "string":
                    valor = valor.get("value", "")

                if isinstance(clave, str) and isinstance(valor, str):
                    datos_normalizados[clave] = valor

            datos = datos_normalizados

        if not isinstance(datos, dict):
            continue

        texto_opcion = datos.get("title") or datos.get("text") or ""

        if not isinstance(texto_opcion, str):
            continue

        if _normalizar_oficina(texto_opcion) != oficina_solicitada:
            continue

        await opciones[indice].click()
        await page.sleep(random.uniform(0.6, 1.0))

        print(
            f"[Acción] Oficina seleccionada correctamente: "
            f"{texto_opcion.strip()}",
            flush=True,
        )
        return True

        if not isinstance(texto_opcion, str):
            continue

        if _normalizar_oficina(texto_opcion) != oficina_solicitada:
            continue

        await opcion.click()
        await page.sleep(random.uniform(0.6, 1.0))

        print(
            f"[Acción] Oficina seleccionada correctamente: "
            f"{texto_opcion.strip()}"
        )
        return True

    print(
        f"[-] No hay coincidencia exacta para la oficina "
        f"{oficina!r}. No se seleccionará otra oficina."
    )
    return False
