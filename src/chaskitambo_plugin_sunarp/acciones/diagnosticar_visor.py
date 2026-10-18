from __future__ import annotations

import json
import logging
from typing import Any

import nodriver as uc

logger = logging.getLogger("chaskitambo.plugin.sunarp")


async def diagnosticar_visor(page: uc.Tab) -> dict[str, Any]:
    """Inspecciona el visor SUNARP sin modificar su estado."""

    script = r"""
    (() => {
        const normalizar = (texto) =>
            (texto || "").replace(/\s+/g, " ").trim();

        const selectores = [
            ".side-bar-container",
            ".columna-lista",
            ".row-container",
            ".pagina",
            ".boton-pagina",
            ".cabecera-container",
            "iframe",
            "embed",
            "object",
            "canvas",
            "pdf-viewer",
            "#viewer",
            "#viewerContainer",
            ".pdfViewer"
        ];

        const elementos = [];

        for (const selector of selectores) {
            const encontrados = Array.from(
                document.querySelectorAll(selector)
            );

            for (const elemento of encontrados.slice(0, 80)) {
                elementos.push({
                    selector,
                    tag: elemento.tagName,
                    id: elemento.id || null,
                    clases: typeof elemento.className === "string"
                        ? elemento.className
                        : null,
                    texto: normalizar(elemento.innerText).slice(0, 500),
                    titulo: elemento.getAttribute("title"),
                    ariaLabel: elemento.getAttribute("aria-label"),
                    href: elemento.getAttribute("href"),
                    src: elemento.getAttribute("src"),
                    data: Array.from(elemento.attributes)
                        .filter(a => a.name.startsWith("data-"))
                        .reduce((obj, a) => {
                            obj[a.name] = a.value;
                            return obj;
                        }, {})
                });
            }
        }

        const app = window.PDFViewerApplication;
        const doc = app?.pdfDocument;

        return JSON.stringify({
            url: location.href,
            titulo: document.title,
            readyState: document.readyState,
            partidaCabecera: normalizar(
                document.querySelector(".cabecera-container")?.innerText
            ),
            pdfjs: {
                existeAplicacion: Boolean(app),
                existeDocumento: Boolean(doc),
                numeroPaginas: doc?.numPages ?? null,
                urlDocumento: app?.url ?? null,
                urlBase: app?.baseUrl ?? null
            },
            elementos
        });
    })()
    """

    resultado = await page.evaluate(script, await_promise=True)

    if isinstance(resultado, str):
        datos = json.loads(resultado)
    elif isinstance(resultado, dict):
        datos = resultado
    else:
        raise RuntimeError(
            "Respuesta inesperada del diagnóstico: "
            f"{type(resultado).__name__}"
        )

    logger.info(
        "Diagnóstico del visor:\n%s",
        json.dumps(datos, ensure_ascii=False, indent=2),
    )

    return datos