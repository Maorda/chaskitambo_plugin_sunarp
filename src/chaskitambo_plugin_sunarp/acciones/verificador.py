
# Archivo: src/chaskitambo_plugin_sunarp/acciones/verificador.py

import json
import re

import nodriver as uc


async def verificar_estado_y_previsualizar(
    page: uc.Tab,
) -> list[dict] | None:
    """
    Abre la previsualización y extrae los asientos identificados
    en la barra lateral de SUNARP.

    No confunde el número de partida con el número de asiento.
    Devuelve None si no puede validar la tabla.
    """
    print("[Acción] Validando respuestas del servidor...")
    await page.sleep(0.5)

    try:
        cero_registros = await page.find(
            "Total de registros encontrados: 0",
            timeout=2,
        )
        if cero_registros:
            print("[-] SUNARP no encontró registros.")
            return None
    except Exception:
        pass

    modal_error = await page.select(
        ".modal-body, .alert-danger, snack-bar-container"
    )

    if modal_error and "bad SQL grammar" in modal_error.text.lower():
        print("[-] SUNARP devolvió un error SQL.")
        await page.save_screenshot("error_sql_sunarp.png")
        return None

    # El HTML real utiliza button.btn-search.
    boton = await page.select(
        'button.btn-search[title="Previsualizar"]',
        timeout=5,
    )

    if not boton:
        print("[-] No se encontró el botón de previsualización.")
        return None

    await boton.click()
    await page.sleep(2.0)

    js_script = r"""
    (() => {
        const normalizar = (texto) =>
            (texto || "").replace(/\s+/g, " ").trim();

        const contenedores = Array.from(
            document.querySelectorAll(".side-bar-container")
        );

        const contenedor = contenedores.find(
            el => el.querySelector(".columna-lista .row-container")
        );

        if (!contenedor) {
            return JSON.stringify({
                error: "No se encontró la lista de asientos en la barra lateral."
            });
        }

        const cabecera = normalizar(
            contenedor.querySelector(".cabecera-container")?.innerText
        );

        const partidaMatch = cabecera.match(
            /Partida\s*:\s*([^\s]+)/i
        );

        const asientos = [];
        const identificadoresVistos = new Set();

        for (const elemento of contenedor.querySelectorAll(
            ".columna-lista"
        )) {
            const fila = elemento.querySelector(".row-container");
            if (!fila) continue;

            const bloques = Array.from(fila.children);
            const textoPrincipal = normalizar(
                bloques[0]?.innerText
            );

            const match = textoPrincipal.match(
                /N\s*[°ºo.]?\s*Asiento\s*:\s*([^\s]+)/i
            );

            if (!match) continue;

            const numeroAsiento = normalizar(match[1]);

            // No registrar dos veces el mismo número de asiento.
            if (identificadoresVistos.has(numeroAsiento)) {
                return JSON.stringify({
                    error: "Número de asiento duplicado en la lista: "
                         + numeroAsiento
                });
            }

            identificadoresVistos.add(numeroAsiento);

            const actoMatch = textoPrincipal.match(
                /Acto\s*:\s*([\s\S]*)$/i
            );

            const textoAnio = normalizar(
                fila.querySelector(".anio")?.innerText
            );

            const anioMatch = textoAnio.match(
                /Año\s*:\s*(\d{4})/i
            );

            const rubroMatch = textoAnio.match(
                /Rubro\s*:\s*([\s\S]*)$/i
            );

            const paginas = Array.from(
                fila.querySelectorAll(".pagina .boton-pagina")
            ).map(span => normalizar(span.innerText))
             .filter(Boolean);

            asientos.push({
                asiento: numeroAsiento,
                acto: normalizar(actoMatch?.[1] || ""),
                anio: anioMatch ? anioMatch[1] : null,
                rubro: normalizar(rubroMatch?.[1] || ""),
                paginas: paginas,
                descripcion: textoPrincipal,
            });
        }

        if (!asientos.length) {
            return JSON.stringify({
                error: "La barra lateral no contiene asientos identificables."
            });
        }

        return JSON.stringify({
            partida_registral: partidaMatch ? partidaMatch[1] : null,
            total_asientos_listados: asientos.length,
            asientos: asientos,
        });
    })()
    """

    try:
        resultado = await page.evaluate(
            js_script,
            await_promise=True,
        )
        datos = json.loads(resultado) if isinstance(resultado, str) else None
    except Exception as exc:
        print(f"[-] Error leyendo la lista de asientos: {exc}")
        return None

    if not isinstance(datos, dict) or datos.get("error"):
        motivo = (
            datos.get("error", "Respuesta inesperada del navegador.")
            if isinstance(datos, dict)
            else "El navegador no devolvió un objeto JSON."
        )
        print(f"[-] {motivo}")
        await page.save_screenshot("asientos_no_identificados.png")
        return None

    partida = datos.get("partida_registral")
    asientos = datos.get("asientos")

    if not partida or not isinstance(asientos, list) or not asientos:
        print("[-] No se pudo validar la partida o la lista de asientos.")
        return None

    for asiento in asientos:
        print(
            f"[OK] Asiento {asiento['asiento']} | "
            f"Acto: {asiento['acto']} | "
            f"Año: {asiento['anio']} | "
            f"Páginas: {asiento['paginas']}"
        )

    print(
        f"[OK] Partida {partida}: "
        f"{len(asientos)} asientos identificados."
    )

    return asientos
