from __future__ import annotations

import json
import logging
from typing import Any

import nodriver as uc

logger = logging.getLogger("chaskitambo.plugin.sunarp")


async def diagnosticar_asientos(page: uc.Tab) -> dict[str, Any]:
    """Inspecciona filas, páginas y controles del visor SUNARP."""

    script = r"""
    (() => {
        const normalizar = texto =>
            (texto || "").replace(/\s+/g, " ").trim();

        const describir = elemento => {
            if (!elemento) return null;

            const rect = elemento.getBoundingClientRect();

            return {
                tag: elemento.tagName,
                texto: normalizar(elemento.innerText),
                clase: typeof elemento.className === "string"
                    ? elemento.className
                    : "",
                id: elemento.id || null,
                role: elemento.getAttribute("role"),
                tabindex: elemento.getAttribute("tabindex"),
                cursor: getComputedStyle(elemento).cursor,
                onclick: elemento.getAttribute("onclick"),
                ariaLabel: elemento.getAttribute("aria-label"),
                title: elemento.getAttribute("title"),
                rect: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                },
                html: elemento.outerHTML.slice(0, 3000)
            };
        };

        const filas = Array.from(
            document.querySelectorAll(
                ".side-bar-container .columna-lista .row-container"
            )
        );

        const asientos = filas.map((fila, indice) => ({
            indice,
            fila: describir(fila),
            elementos: Array.from(fila.querySelectorAll("*"))
                .map(describir),
            paginas: Array.from(
                fila.querySelectorAll(".pagina .boton-pagina")
            ).map(describir)
        }));

        return JSON.stringify({
            url: location.href,
            titulo: document.title,
            cabecera: normalizar(
                document.querySelector(
                    ".side-bar-container .cabecera-container"
                )?.innerText
            ),
            asientos,
            scriptsAngular: Array.from(
                document.querySelectorAll("script[src]")
            )
                .map(s => s.src)
                .filter(src => /main|runtime|polyfills/i.test(src))
        });
    })()
    """

    resultado = await page.evaluate(
        script,
        await_promise=True,
    )

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
        "[DIAGNOSTICO] DOM de asientos:\n%s",
        json.dumps(datos, ensure_ascii=False, indent=2),
    )

    return datos