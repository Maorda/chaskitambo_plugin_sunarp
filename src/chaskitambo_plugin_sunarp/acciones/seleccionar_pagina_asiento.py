
from __future__ import annotations

import json
import logging

import nodriver as uc

logger = logging.getLogger("chaskitambo.plugin.sunarp")


async def seleccionar_pagina_asiento(
    page: uc.Tab,
    numero_asiento: str,
    numero_pagina: str = "1",
) -> bool:
    """
    Selecciona la página de un asiento concreto en la barra lateral SUNARP.

    Pulsa el elemento span.boton-pagina correspondiente.
    No descarga el PDF ni confirma que su carga haya terminado.
    """

    asiento_objetivo = str(numero_asiento or "").strip()
    pagina_objetivo = str(numero_pagina or "").strip()

    if not asiento_objetivo or not pagina_objetivo:
        raise ValueError(
            "El número de asiento y el número de página son obligatorios."
        )

    parametros = json.dumps({
        "asiento": asiento_objetivo,
        "pagina": pagina_objetivo,
    }, ensure_ascii=False)

    script = r"""
    (parametrosJSON) => {
        const parametros = JSON.parse(parametrosJSON);
        const asientoObjetivo = parametros.asiento;
        const paginaObjetivo = parametros.pagina;

        const normalizar = texto =>
            (texto || "").replace(/\s+/g, " ").trim();

        const filas = Array.from(
            document.querySelectorAll(
                ".side-bar-container .columna-lista .row-container"
            )
        );

        const fila = filas.find(elemento => {
            const texto = normalizar(elemento.innerText);
            const coincidencia = texto.match(
                /N\s*[°ºo.]?\s*Asiento\s*:\s*([^\s]+)/i
            );

            return coincidencia &&
                normalizar(coincidencia[1]) === asientoObjetivo;
        });

        if (!fila) {
            return JSON.stringify({
                ok: false,
                fase: "buscar_asiento",
                error: "No se encontró el asiento solicitado.",
                asiento: asientoObjetivo
            });
        }

        const botones = Array.from(
            fila.querySelectorAll(".pagina .boton-pagina")
        );

        const boton = botones.find(
            elemento =>
                normalizar(elemento.innerText) === paginaObjetivo
        );

        if (!boton) {
            return JSON.stringify({
                ok: false,
                fase: "buscar_pagina",
                error: "No se encontró la página en el asiento.",
                asiento: asientoObjetivo,
                pagina: paginaObjetivo,
                paginas_disponibles: botones.map(
                    elemento => normalizar(elemento.innerText)
                )
            });
        }

        boton.click();

        return JSON.stringify({
            ok: true,
            fase: "click_realizado",
            asiento: asientoObjetivo,
            pagina: paginaObjetivo,
            texto_fila: normalizar(fila.innerText)
        });
    }
    """

    try:
        script_con_parametros = (
            f"({script})({json.dumps(parametros, ensure_ascii=False)})"
        )

        resultado = await page.evaluate(
            script_con_parametros,
            await_promise=True,
            return_by_value=True,
        )
    except Exception:
        logger.exception(
            "[ASIENTO] Error al pulsar la página %s del asiento %s.",
            pagina_objetivo,
            asiento_objetivo,
        )
        raise

    if isinstance(resultado, str):
        try:
            resultado = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "La respuesta de Nodriver no contiene JSON válido."
            ) from exc

    if not isinstance(resultado, dict):
        raise RuntimeError(
            "Respuesta inesperada al seleccionar la página: "
            f"{type(resultado).__name__}"
        )

    if not resultado.get("ok"):
        logger.error(
            "[ASIENTO] No se pudo seleccionar asiento=%s, página=%s: %s",
            asiento_objetivo,
            pagina_objetivo,
            resultado,
        )
        return False

    logger.info(
        "[ASIENTO] Se pulsó la página %s del asiento %s.",
        pagina_objetivo,
        asiento_objetivo,
    )

    return True
