
# Archivo:
# D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\acciones\esperar_pdf_listo.py

from __future__ import annotations

import json
import logging

import nodriver as uc

logger = logging.getLogger("chaskitambo.plugin.sunarp")

TIEMPO_MAX_ESPERA_PDF = 25 * 60
INTERVALO_COMPROBACION_MS = 250


async def preparar_espera_pdf(page: uc.Tab) -> None:
    """Instala la observación del visor antes de seleccionar una página."""
    script = r"""
    (() => {
        const app = window.PDFViewerApplication;
        if (!app) {
            throw new Error("PDFViewerApplication no está disponible.");
        }

        const anterior = window.__sunarpPdfWait;
        if (anterior?.observer) anterior.observer.disconnect();

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

        const estado = {
            documentoInicial: app.pdfDocument ?? null,
            spinnerVisto: spinnerVisible(),
            observer: null
        };

        estado.observer = new MutationObserver(() => {
            if (spinnerVisible()) estado.spinnerVisto = true;
        });

        estado.observer.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true
        });

        window.__sunarpPdfWait = estado;

        return JSON.stringify({
            ok: true,
            documentoInicialDisponible: !!estado.documentoInicial
        });
    })()
    """

    resultado = await page.evaluate(
        script,
        await_promise=True,
        return_by_value=True,
    )

    if isinstance(resultado, dict):
        datos = resultado

    elif isinstance(resultado, str):
        try:
            datos = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "El visor devolvió una cadena que no es JSON. "
                f"Respuesta={resultado[:1000]!r}"
            ) from exc

    else:
        tipo = (
            f"{type(resultado).__module__}."
            f"{type(resultado).__qualname__}"
        )

        descripcion = getattr(resultado, "text", None)
        excepcion = getattr(resultado, "exception", None)

        if excepcion is not None:
            detalle = getattr(excepcion, "description", None)
            if detalle:
                descripcion = detalle

        logger.error(
            "Respuesta inesperada al preparar el visor PDF. "
            "Tipo=%s; respuesta=%r; detalle=%r",
            tipo,
            resultado,
            descripcion,
        )

        raise RuntimeError(
            "No se pudo preparar el visor PDF. "
            f"Tipo de respuesta={tipo}; detalle={descripcion!r}"
        )

    if not datos.get("ok"):
        raise RuntimeError("No se pudo preparar la espera del visor PDF.")

    logger.debug(
        "Observador PDF instalado; documento inicial disponible=%s.",
        datos.get("documentoInicialDisponible"),
    )


async def esperar_pdf_listo(
    page: uc.Tab,
    timeout_seconds: int = TIEMPO_MAX_ESPERA_PDF,
) -> dict[str, int | bool]:
    """Espera el cambio al documento PDF nuevo y comprueba su disponibilidad."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds debe ser mayor que cero.")

    parametros = {
        "timeout_ms": timeout_seconds * 1000,
        "intervalo_ms": INTERVALO_COMPROBACION_MS,
    }

    script = f"""
    (async () => {{
        const parametros = JSON.parse({json.dumps(json.dumps(parametros))});
        const estado = window.__sunarpPdfWait;

        if (!estado) {{
            throw new Error("No se preparó el observador antes del clic.");
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
                const app = window.PDFViewerApplication;
                const doc = app?.pdfDocument ?? null;
                const cargando = spinnerVisible();

                const documentoNuevo = !!doc
                    && doc !== estado.documentoInicial
                    && typeof doc.getData === "function"
                    && Number.isInteger(doc.numPages)
                    && doc.numPages > 0;

                if (documentoNuevo && !cargando) {{
                    try {{
                        await doc.getPage(1);

                        if (
                            window.PDFViewerApplication?.pdfDocument === doc
                            && !spinnerVisible()
                        ) {{
                            const resultado = {{
                                ok: true,
                                paginas: doc.numPages,
                                duracionMs: Date.now() - inicio,
                                spinnerVisto: estado.spinnerVisto
                            }};

                            estado.observer?.disconnect();
                            window.__sunarpPdfWait = null;
                            return JSON.stringify(resultado);
                        }}
                    }} catch (error) {{
                        // PDF.js puede seguir inicializando el documento.
                    }}
                }}

                await new Promise(resolve =>
                    setTimeout(resolve, parametros.intervalo_ms)
                );
            }}

            throw new Error(
                "Tiempo agotado o no se confirmó el cambio al nuevo PDF."
            );
        }} catch (error) {{
            estado.observer?.disconnect();
            window.__sunarpPdfWait = null;

            return JSON.stringify({{
                ok: false,
                error: String(error)
            }});
        }}
    }})()
    """

    resultado = await page.evaluate(
        script,
        await_promise=True,
        return_by_value=True,
    )

    if isinstance(resultado, dict):
        datos = resultado
    elif isinstance(resultado, str):
        datos = json.loads(resultado)
    else:
        raise RuntimeError("Respuesta inesperada durante la espera del PDF.")

    if not datos.get("ok"):
        raise RuntimeError(
            str(datos.get("error", "No se pudo confirmar la carga del PDF."))
        )

    logger.info(
        "Visor listo: páginas=%s; duración=%s ms.",
        datos.get("paginas"),
        datos.get("duracionMs"),
    )
    return datos
