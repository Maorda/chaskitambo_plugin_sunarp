
# Archivo: D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\acciones\esperar_pdf_listo.py

from __future__ import annotations

import json
import logging
from typing import Any

import nodriver as uc

logger = logging.getLogger("chaskitambo.plugin.sunarp")

TIEMPO_MAX_ESPERA_PDF = 25 * 60
INTERVALO_COMPROBACION_MS = 250


def _detalle_error_nodriver(resultado: Any) -> str:
    """Extrae detalles útiles de ExceptionDetails u otras respuestas."""
    excepcion = getattr(resultado, "exception", None)

    if excepcion is not None:
        descripcion = getattr(excepcion, "description", None)
        if descripcion:
            return str(descripcion)

    texto = getattr(resultado, "text", None)
    if texto:
        return str(texto)

    return repr(resultado)


def _decodificar_respuesta(resultado: Any, operacion: str) -> dict:
    """Normaliza las respuestas de evaluate de Nodriver."""
    if isinstance(resultado, dict):
        return resultado

    if isinstance(resultado, str):
        try:
            datos = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{operacion}: Nodriver devolvió texto que no es JSON. "
                f"Respuesta={resultado[:1000]!r}"
            ) from exc

        if isinstance(datos, dict):
            return datos

        raise RuntimeError(
            f"{operacion}: el JSON recibido no es un objeto."
        )

    raise RuntimeError(
        f"{operacion}: respuesta inesperada de Nodriver. "
        f"Tipo={type(resultado).__module__}."
        f"{type(resultado).__qualname__}; "
        f"detalle={_detalle_error_nodriver(resultado)}"
    )


async def preparar_espera_pdf(page: uc.Tab) -> None:
    """
    Instala un observador antes de seleccionar una página.

    PDFViewerApplication y pdfDocument pueden no estar disponibles todavía.
    Esa ausencia inicial no se considera un error: la espera posterior
    comprobará cuándo aparece el documento PDF.
    """
    script = r"""
    (() => {
        const anterior = window.__sunarpPdfWait;

        if (anterior?.observer) {
            anterior.observer.disconnect();
        }

        const spinnerVisible = () => {
            return Array.from(
                document.querySelectorAll(".ngx-spinner-overlay")
            ).some(elemento => {
                const estilo = getComputedStyle(elemento);
                const rect = elemento.getBoundingClientRect();

                return estilo.display !== "none"
                    && estilo.visibility !== "hidden"
                    && Number(estilo.opacity) > 0
                    && rect.width > 0
                    && rect.height > 0;
            });
        };

        const appInicial = window.PDFViewerApplication ?? null;
        const documentoInicial = appInicial?.pdfDocument ?? null;

        const estado = {
            appInicial,
            documentoInicial,
            spinnerVisto: spinnerVisible(),
            observer: null,
            preparadoEn: Date.now()
        };

        estado.observer = new MutationObserver(() => {
            if (spinnerVisible()) {
                estado.spinnerVisto = true;
            }
        });

        estado.observer.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true
        });

        window.__sunarpPdfWait = estado;

        return JSON.stringify({
            ok: true,
            aplicacionInicialDisponible: !!appInicial,
            documentoInicialDisponible: !!documentoInicial,
            spinnerInicialVisible: estado.spinnerVisto
        });
    })()
    """

    try:
        resultado = await page.evaluate(
            script,
            await_promise=True,
            return_by_value=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo instalar el observador del visor PDF: {exc}"
        ) from exc

    datos = _decodificar_respuesta(
        resultado,
        "Preparación del visor PDF",
    )

    if not datos.get("ok"):
        raise RuntimeError(
            "No se pudo instalar el observador del visor PDF: "
            f"{datos!r}"
        )

    logger.info(
        "[PDF] Observador instalado. "
        "Aplicación inicial=%s; documento inicial=%s; spinner inicial=%s.",
        datos.get("aplicacionInicialDisponible"),
        datos.get("documentoInicialDisponible"),
        datos.get("spinnerInicialVisible"),
    )


async def esperar_pdf_listo(
    page: uc.Tab,
    timeout_seconds: int = TIEMPO_MAX_ESPERA_PDF,
) -> dict[str, int | bool]:
    """
    Espera hasta detectar un documento PDF válido distinto del inicial.

    No considera suficiente que exista PDFViewerApplication: también
    exige pdfDocument, getData(), un número de páginas válido y que
    el spinner no esté visible.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds debe ser mayor que cero.")

    parametros = {
        "timeout_ms": timeout_seconds * 1000,
        "intervalo_ms": INTERVALO_COMPROBACION_MS,
    }

    parametros_json = json.dumps(parametros)

    script = f"""
    (async () => {{
        const parametros = JSON.parse(
            {json.dumps(parametros_json)}
        );

        const estado = window.__sunarpPdfWait;

        if (!estado) {{
            return JSON.stringify({{
                ok: false,
                error: "No se instaló el observador antes de seleccionar la página."
            }});
        }}

        const spinnerVisible = () => {{
            return Array.from(
                document.querySelectorAll(".ngx-spinner-overlay")
            ).some(elemento => {{
                const estilo = getComputedStyle(elemento);
                const rect = elemento.getBoundingClientRect();

                return estilo.display !== "none"
                    && estilo.visibility !== "hidden"
                    && Number(estilo.opacity) > 0
                    && rect.width > 0
                    && rect.height > 0;
            }});
        }};

        const inicio = Date.now();

        try {{
            while (Date.now() - inicio < parametros.timeout_ms) {{
                const app = window.PDFViewerApplication ?? null;
                const doc = app?.pdfDocument ?? null;
                const cargando = spinnerVisible();

                if (cargando) {{
                    estado.spinnerVisto = true;
                }}

                const documentoDisponible =
                    !!doc
                    && typeof doc.getData === "function"
                    && Number.isInteger(doc.numPages)
                    && doc.numPages > 0;

                const documentoNuevo =
                    documentoDisponible
                    && doc !== estado.documentoInicial;

                if (documentoNuevo && !cargando) {{
                    try {{
                        await doc.getPage(1);

                        const appActual =
                            window.PDFViewerApplication ?? null;

                        const docActual = appActual?.pdfDocument ?? null;

                        if (
                            docActual === doc
                            && typeof docActual.getData === "function"
                            && Number.isInteger(docActual.numPages)
                            && docActual.numPages > 0
                            && !spinnerVisible()
                        ) {{
                            const resultado = {{
                                ok: true,
                                paginas: docActual.numPages,
                                duracionMs: Date.now() - inicio,
                                spinnerVisto: estado.spinnerVisto,
                                aplicacionInicialDisponible:
                                    !!estado.appInicial,
                                documentoInicialDisponible:
                                    !!estado.documentoInicial,
                                documentoNuevo: true
                            }};

                            estado.observer?.disconnect();
                            window.__sunarpPdfWait = null;

                            return JSON.stringify(resultado);
                        }}
                    }} catch (error) {{
                        // El visor puede estar inicializando la primera página.
                    }}
                }}

                await new Promise(resolve =>
                    setTimeout(resolve, parametros.intervalo_ms)
                );
            }}

            const appFinal = window.PDFViewerApplication ?? null;
            const docFinal = appFinal?.pdfDocument ?? null;

            return JSON.stringify({{
                ok: false,
                error:
                    "Tiempo agotado sin confirmar un documento PDF nuevo y listo.",
                aplicacionDisponible: !!appFinal,
                documentoDisponible: !!docFinal,
                getDataDisponible:
                    typeof docFinal?.getData === "function",
                paginas: docFinal?.numPages ?? null,
                spinnerVisible: spinnerVisible(),
                spinnerVisto: estado.spinnerVisto,
                documentoSigueSiendoElInicial:
                    !!docFinal && docFinal === estado.documentoInicial,
                duracionMs: Date.now() - inicio
            }});
        }} finally {{
            estado.observer?.disconnect();
            window.__sunarpPdfWait = null;
        }}
    }})()
    """

    try:
        resultado = await page.evaluate(
            script,
            await_promise=True,
            return_by_value=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Nodriver no pudo esperar la carga del PDF: {exc}"
        ) from exc

    datos = _decodificar_respuesta(
        resultado,
        "Espera del visor PDF",
    )

    if not datos.get("ok"):
        raise RuntimeError(
            "No se confirmó la carga del PDF. "
            f"Diagnóstico={json.dumps(datos, ensure_ascii=False)}"
        )

    logger.info(
        "[PDF] Visor listo: páginas=%s; duración=%s ms; "
        "spinner visto=%s; documento inicial disponible=%s.",
        datos.get("paginas"),
        datos.get("duracionMs"),
        datos.get("spinnerVisto"),
        datos.get("documentoInicialDisponible"),
    )

    return datos
