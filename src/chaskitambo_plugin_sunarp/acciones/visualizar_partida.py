from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any
import nodriver as uc
logger = logging.getLogger("chaskitambo.plugin.sunarp")
RUTA_VISUALIZACION = (
    "/conoce-aqui/servicio/busqueda/visualizar-partida"
)
TIMEOUT_PRIMER_SPINNER = 15.0
TIMEOUT_URL = 20.0
TIMEOUT_SEGUNDO_SPINNER = 25.0
INTERVALO_DIAGNOSTICO = 0.25
_JS_ESTADO_VISUALIZACION = r"""
(() => {
    const visible = (elemento) => {
        if (!elemento) return false;

        const estilo = window.getComputedStyle(elemento);
        const rect = elemento.getBoundingClientRect();

        return (
            estilo.display !== "none"
            && estilo.visibility !== "hidden"
            && Number(estilo.opacity) > 0
            && rect.width > 0
            && rect.height > 0
        );
    };

    const selectoresSpinner = [
        ".ant-spin",
        ".spinner",
        ".loading-overlay",
        ".ngx-spinner-overlay",
        ".ant-modal-loading",
        "[aria-busy='true']"
    ];

    const spinners = [];

    for (const selector of selectoresSpinner) {
        for (const elemento of document.querySelectorAll(selector)) {
            if (!visible(elemento)) continue;

            const entrada = {
                selector,
                clase: String(elemento.className || ""),
                texto: (elemento.innerText || "")
                    .replace(/\s+/g, " ")
                    .trim().slice(0, 120)
            };

            spinners.push(entrada);
        }
    }

    const textoPagina = (document.body?.innerText || "")
        .replace(/\s+/g, " ")
        .trim();

    const contenedores = Array.from(
        document.querySelectorAll(".side-bar-container")
    );

    const barraAsientos = contenedores.some(
        elemento =>
            elemento.querySelector(
                ".columna-lista .row-container"
            ) !== null
    );

    const partidasDetectadas = [];

    for (const elemento of document.querySelectorAll(
        ".cabecera-container"
    )) {
        const texto = (elemento.innerText || "")
            .replace(/\s+/g, " ")
            .trim();

        const coincidencia = texto.match(
            /Partida\s*:\s*([^\s]+)/i
        );

        if (coincidencia) {
            partidasDetectadas.push(coincidencia[1]);
        }
    }

    return JSON.stringify({
        url: window.location.href,
        titulo: document.title,
        spinner_visible: spinners.length > 0,
        spinners: spinners,
        barra_asientos_detectada: barraAsientos,
        partidas_detectadas: partidasDetectadas,
        texto_contiene_asientos: /N\s*[°ºo.]?\s*Asiento\s*:/i
            .test(textoPagina),
        texto_pagina_inicio: textoPagina.slice(0, 500)
    });
})()
"""


async def _leer_estado(page: uc.Tab) -> dict[str, Any]:
    respuesta = await page.evaluate(
        _JS_ESTADO_VISUALIZACION,
        await_promise=True,
    )

    if isinstance(respuesta, str):
        respuesta = json.loads(respuesta)

    if not isinstance(respuesta, dict):
        raise RuntimeError(
            "SUNARP devolvió un estado de visualización inválido."
        )

    return respuesta


async def _esperar(
    page: uc.Tab,
    condicion,
    timeout: float,
    descripcion: str,
) -> dict[str, Any] | None:
    inicio = time.monotonic()
    ultimo_estado: dict[str, Any] | None = None

    while time.monotonic() - inicio < timeout:
        ultimo_estado = await _leer_estado(page)

        if condicion(ultimo_estado):
            return ultimo_estado

        await asyncio.sleep(INTERVALO_DIAGNOSTICO)

    logger.warning(
        "Tiempo agotado esperando: %s. Último estado=%s",
        descripcion,
        ultimo_estado,
    )

    return None


async def visualizar_partida(
    page: uc.Tab,
    partida_objetivo: str,
) -> dict[str, Any]:
    """
    Pulsa Previsualizar en la fila de la partida exacta y
    diagnostica las dos fases de carga de SUNARP.

    No descarga PDF ni modifica la sesión del navegador.
    """
    partida = str(partida_objetivo or "").strip()

    if not partida:
        raise ValueError(
            "No se puede visualizar una partida vacía."
        )

    logger.info(
        "[VISUALIZACIÓN] Preparando la partida %s.",
        partida,
    )

    # Seleccionar el botón de la fila exacta.
    
    js_clic = r"""
    (() => {
        const partida = __PARTIDA_OBJETIVO_JSON__;

        const normalizar = valor =>
            (valor || "").replace(/\s+/g, "").toUpperCase();

        const filas = Array.from(
            document.querySelectorAll(
                "#results .table-complete "
                + "tbody tr.ant-table-row:not(.detail)"
            )
        );

        const fila = filas.find(elemento => {
            const celdas = Array.from(
                elemento.querySelectorAll("td")
            );

            return normalizar(celdas[2]?.innerText) ===
                normalizar(partida);
        });

        if (!fila) {
            return JSON.stringify({
                ok: false,
                motivo: "No se encontró la fila exacta de la partida.",
                filas_encontradas: filas.length
            });
        }

        const boton = fila.querySelector(
            'button[title="Previsualizar"]'
        );

        if (!boton) {
            return JSON.stringify({
                ok: false,
                motivo: "La fila no contiene el botón Previsualizar."
            });
        }

        if (boton.disabled) {
            return JSON.stringify({
                ok: false,
                motivo: "El botón Previsualizar está deshabilitado."
            });
        }

        boton.click();

        return JSON.stringify({
            ok: true,
            partida: partida,
            boton: boton.outerHTML.slice(0, 300)
        });
    })()
    """

    js_clic_con_partida = js_clic.replace(
        "__PARTIDA_OBJETIVO_JSON__",
        json.dumps(partida),
    )

    resultado_clic = await page.evaluate(
        js_clic_con_partida,
        await_promise=True,
    )

    if isinstance(resultado_clic, str):
        resultado_clic = json.loads(resultado_clic)

    if not isinstance(resultado_clic, dict):
        raise RuntimeError(
            "Respuesta inválida al intentar pulsar Previsualizar."
        )

    if not resultado_clic.get("ok"):
        raise RuntimeError(
            "No se pudo iniciar la visualización: "
            + str(resultado_clic.get("motivo", resultado_clic))
        )


    logger.info(
        "[VISUALIZACIÓN] Botón pulsado para la partida %s.",
        partida,
    )

    # Fase 1: observar el primer spinner.
    logger.info(
        "[VISUALIZACIÓN][FASE 1] Esperando el primer spinner."
    )

    primer_spinner = await _esperar(
        page,
        lambda estado: estado.get("spinner_visible") is True,
        TIMEOUT_PRIMER_SPINNER,
        "aparición del primer spinner",
    )

    if primer_spinner is None:
        logger.warning(
            "[VISUALIZACIÓN][FASE 1] No se observó el spinner. "
            "Puede haber sido demasiado breve o utilizar otro selector."
        )
    else:
        logger.info(
            "[VISUALIZACIÓN][FASE 1] Spinner detectado: %s",
            primer_spinner.get("spinners"),
        )

        desaparecio_primer_spinner = await _esperar(
            page,
            lambda estado: estado.get("spinner_visible") is False,
            TIMEOUT_PRIMER_SPINNER,
            "desaparición del primer spinner",
        )

        if desaparecio_primer_spinner is None:
            raise TimeoutError(
                "El primer spinner no desapareció dentro del plazo."
            )

        logger.info(
            "[VISUALIZACIÓN][FASE 1] Primer spinner desaparecido."
        )

    # Fase 2: comprobar el cambio de URL.
    logger.info(
        "[VISUALIZACIÓN][FASE 2] Esperando la URL de destino."
    )

    estado_url = await _esperar(
        page,
        lambda estado: RUTA_VISUALIZACION in estado.get("url", ""),
        TIMEOUT_URL,
        "cambio a la URL de visualización",
    )

    if estado_url is None:
        estado_actual = await _leer_estado(page)

        logger.error(
            "[VISUALIZACIÓN] No se alcanzó la URL esperada. "
            "URL actual=%s",
            estado_actual.get("url"),
        )

        return {
            "ok": False,
            "fase": "cambio_url",
            "partida_solicitada": partida,
            "url_actual": estado_actual.get("url"),
            "estado": estado_actual,
        }

    logger.info(
        "[VISUALIZACIÓN][FASE 2] URL detectada: %s",
        estado_url.get("url"),
    )

    # Fase 3: observar el segundo spinner.
    logger.info(
        "[VISUALIZACIÓN][FASE 3] Esperando el segundo spinner."
    )

    segundo_spinner = await _esperar(
        page,
        lambda estado: estado.get("spinner_visible") is True,
        TIMEOUT_SEGUNDO_SPINNER,
        "aparición del segundo spinner",
    )

    if segundo_spinner is None:
        logger.warning(
            "[VISUALIZACIÓN][FASE 3] No se observó el segundo spinner. "
            "Se continuará comprobando la vista sin asumir que falló."
        )
    else:
        logger.info(
            "[VISUALIZACIÓN][FASE 3] Spinner detectado: %s",
            segundo_spinner.get("spinners"),
        )

    # Fase 4: esperar a que no haya spinner visible.
    estado_final = await _esperar(
        page,
        lambda estado: estado.get("spinner_visible") is False,
        TIMEOUT_SEGUNDO_SPINNER,
        "finalización de la carga de la vista",
    )

    if estado_final is None:
        estado_final = await _leer_estado(page)

        logger.error(
            "[VISUALIZACIÓN] La carga no se confirmó. URL=%s",
            estado_final.get("url"),
        )

        return {
            "ok": False,
            "fase": "carga_datos",
            "partida_solicitada": partida,
            "url_actual": estado_final.get("url"),
            "estado": estado_final,
        }

    partidas = {
        str(valor).strip().upper()
        for valor in estado_final.get("partidas_detectadas", [])
    }

    coincide_partida = partida.upper() in partidas

    logger.info(
        "[VISUALIZACIÓN][DIAGNÓSTICO] URL=%s",
        estado_final.get("url"),
    )
    logger.info(
        "[VISUALIZACIÓN][DIAGNÓSTICO] Barra de asientos=%s",
        estado_final.get("barra_asientos_detectada"),
    )
    logger.info(
        "[VISUALIZACIÓN][DIAGNÓSTICO] Partidas detectadas=%s",
        estado_final.get("partidas_detectadas"),
    )
    logger.info(
        "[VISUALIZACIÓN][DIAGNÓSTICO] Texto con asientos=%s",
        estado_final.get("texto_contiene_asientos"),
    )
    logger.info(
        "[VISUALIZACIÓN][DIAGNÓSTICO] Inicio del texto=%s",
        estado_final.get("texto_pagina_inicio"),
    )

    # Para esta primera prueba, exigimos la partida correcta.
    if not coincide_partida:
        logger.warning(
            "[VISUALIZACIÓN] La URL se alcanzó, pero no se confirmó "
            "la partida solicitada %s en la cabecera inspeccionada.",
            partida,
        )

    return {
        "ok": coincide_partida,
        "fase": "visualizacion",
        "partida_solicitada": partida,
        "partida_confirmada": coincide_partida,
        "url_actual": estado_final.get("url"),
        "estado": estado_final,
    }
