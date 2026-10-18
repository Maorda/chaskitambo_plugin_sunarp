from __future__ import annotations
import json
import logging
from typing import Any
import nodriver as uc
logger = logging.getLogger("chaskitambo.plugin.sunarp")
_JS_EXTRAER_ASIENTOS = r"""
(() => {
    const normalizar = (texto) =>
        (texto || "").replace(/\s+/g, " ").trim();
    const contenedores = Array.from(
        document.querySelectorAll(".side-bar-container")
    );
    const contenedor = contenedores.find(
        elemento =>
            elemento.querySelector(".columna-lista .row-container")
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
    const partidaRegistral = partidaMatch
        ? normalizar(partidaMatch[1])
        : null;
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
        const asientoMatch = textoPrincipal.match(
            /N\s*[°ºo.]?\s*Asiento\s*:\s*([^\s]+)/i
        );
        if (!asientoMatch) continue;
        const numeroAsiento = normalizar(asientoMatch[1]);
        if (identificadoresVistos.has(numeroAsiento)) {
            return JSON.stringify({
                error:
                    "Número de asiento duplicado en la lista: "
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
        )
            .map(elemento => normalizar(elemento.innerText))
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
    if (!partidaRegistral) {
        return JSON.stringify({
            error: "No se pudo identificar la partida registral en la cabecera."
        });
    }
    if (!asientos.length) {
        return JSON.stringify({
            error: "La barra lateral no contiene asientos identificables.",
            partida_registral: partidaRegistral
        });
    }
    return JSON.stringify({
        partida_registral: partidaRegistral,
        total_asientos_listados: asientos.length,
        asientos: asientos
    });
})()
"""
async def extraer_asientos(
    page: uc.Tab,
    partida_objetivo: str,
) -> dict[str, Any] | None:
    partida = str(partida_objetivo or "").strip().upper()
    if not partida:
        raise ValueError("No se puede extraer asientos de una partida vacía." )
    logger.info("[ASIENTOS] Extrayendo los asientos de la partida %s.",partida,)
    try:
        resultado = await page.evaluate(
            _JS_EXTRAER_ASIENTOS,
            await_promise=True,
        )
    except Exception:
        logger.exception(
            "[ASIENTOS] Error al consultar la lista de asientos."
        )
        return None

    if isinstance(resultado, str):
        try:
            datos = json.loads(resultado)
        except json.JSONDecodeError:
            logger.error(
                "[ASIENTOS] SUNARP devolvió una respuesta JSON inválida: %s",
                resultado,
            )
            return None
    elif isinstance(resultado, dict):
        datos = resultado
    else:
        logger.error(
            "[ASIENTOS] Tipo de respuesta inesperado: %s",
            type(resultado).__name__,
        )
        return None
    if not isinstance(datos, dict):
        logger.error("[ASIENTOS] La respuesta no es un objeto.")
        return None
    if datos.get("error"):
        logger.error(
            "[ASIENTOS] No se pudo extraer la lista: %s",
            datos["error"],
        )
        return None
    partida_detectada = str(
        datos.get("partida_registral") or ""
    ).strip().upper()
    if partida_detectada != partida:
        logger.error(
            "[ASIENTOS] La partida del visor no coincide. "
            "Solicitada=%s; detectada=%s.",
            partida,
            partida_detectada or "(vacía)",
        )
        return None
    asientos = datos.get("asientos")
    if not isinstance(asientos, list) or not asientos:
        logger.error(
            "[ASIENTOS] No se obtuvo una lista válida de asientos."
        )
        return None
    numeros = [
        str(asiento.get("asiento", "")).strip()
        for asiento in asientos
        if isinstance(asiento, dict)
    ]
    if len(numeros) != len(asientos) or any(not numero for numero in numeros):
        logger.error(
            "[ASIENTOS] Hay registros inválidos o números de asiento vacíos."
        )
        return None
    if len(numeros) != len(set(numeros)):
        logger.error(
            "[ASIENTOS] La respuesta contiene números de asiento duplicados."
        )
        return None
    for asiento in asientos:
        paginas = asiento.get("paginas")
        if not isinstance(paginas, list) or not paginas:
            logger.warning(
                "[ASIENTOS] El asiento %s no tiene páginas identificadas.",
                asiento["asiento"],
            )
        logger.info(
            "[ASIENTOS] Asiento=%s; acto=%s; año=%s; rubro=%s; páginas=%s.",
            asiento["asiento"],
            asiento.get("acto", ""),
            asiento.get("anio"),
            asiento.get("rubro", ""),
            paginas,
        )
    datos["partida_registral"] = partida_detectada
    datos["total_asientos_listados"] = len(asientos)
    logger.info(
        "[ASIENTOS] Extracción validada. Partida=%s; asientos=%s.",
        partida_detectada,
        len(asientos),
    )
    return datos
