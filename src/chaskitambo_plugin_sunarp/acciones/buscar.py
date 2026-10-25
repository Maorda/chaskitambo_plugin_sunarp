import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import nodriver as uc
logger = logging.getLogger("chaskitambo.plugin.sunarp")
class EstadoBusqueda(str, Enum):
    REGISTROS_ENCONTRADOS = "registros_encontrados"
    SIN_REGISTROS = "sin_registros"
    TIEMPO_AGOTADO = "tiempo_agotado"
    ERROR_BUSQUEDA = "error_busqueda"
@dataclass
class ResultadoBusqueda:
    estado: EstadoBusqueda
    partida_solicitada: str
    partida_encontrada: bool = False
    registros: list[dict[str, Any]] = field(default_factory=list)
    mensaje: str = ""

    @property
    def total_registros(self) -> int:
        """Cantidad de registros incluidos en el resultado de búsqueda."""
        return len(self.registros)


_JS_INSTALAR_OBSERVADOR = r"""
(() => {
    if (window.__sunarpBusquedaObserver) {
        window.__sunarpBusquedaObserver.disconnect();
    }

    window.__sunarpBusquedaMutaciones = 0;

    window.__sunarpBusquedaObserver = new MutationObserver(() => {
        window.__sunarpBusquedaMutaciones += 1;
    });

    window.__sunarpBusquedaObserver.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true
    });

    return JSON.stringify({ok: true});
})()
"""


_JS_LEER_ESTADO = r"""
(() => {
    const raiz = document.querySelector("#results");

    if (!raiz) {
        return JSON.stringify({
            existe: false,
            mutaciones: window.__sunarpBusquedaMutaciones || 0,
            titulo: "",
            registros: [],
            sinDatos: false,
            spinner: Boolean(
                document.querySelector(
                    ".ant-spin, .spinner, .loading-overlay"
                )
            )
        });
    }

    const tituloElemento = raiz.querySelector(".title-resultado");
    const titulo = (tituloElemento?.innerText || "")
        .replace(/\s+/g, " ")
        .trim();

    const filas = Array.from(
        raiz.querySelectorAll(
            ".table-complete tbody tr.ant-table-row:not(.detail)"
        )
    );

    const registros = filas.map((fila) => {
        const celdas = Array.from(fila.querySelectorAll("td"));
        const valores = celdas.map((celda) =>
            (celda.innerText || "")
                .replace(/\s+/g, " ")
                .trim()
        );

        return {
            zona_registral: valores[0] || "",
            oficina_registral: valores[1] || "",
            partida: valores[2] || "",
            registro: valores[3] || "",
            tiene_hoja_resumen: Boolean(
                fila.querySelector('button[title="Ver Hoja Resumen"]')
            ),
            tiene_previsualizacion: Boolean(
                fila.querySelector('button[title="Previsualizar"]')
            )
        };
    });

    const descripcionVacia = (
        raiz.querySelector(
            ".table-complete .ant-empty-description"
        )?.innerText || ""
    ).replace(/\s+/g, " ").trim();

    const sinDatos = (
        /total de registros encontrados:\s*0/i.test(titulo)
        || /no hay datos/i.test(descripcionVacia)
    );

    return JSON.stringify({
        existe: true,
        mutaciones: window.__sunarpBusquedaMutaciones || 0,
        titulo,
        registros,
        sinDatos,
        spinner: Boolean(
            document.querySelector(
                ".ant-spin, .spinner, .loading-overlay"
            )
        )
    });
})()
"""


_JS_RETIRAR_OBSERVADOR = r"""
(() => {
    if (window.__sunarpBusquedaObserver) {
        window.__sunarpBusquedaObserver.disconnect();
        window.__sunarpBusquedaObserver = null;
    }

    return true;
})()
"""


def _normalizar_partida(valor: Any) -> str:
    return re.sub(r"\s+", "", str(valor or "")).upper()


async def _leer_estado(page: uc.Tab) -> dict[str, Any]:
    respuesta = await page.evaluate(
        _JS_LEER_ESTADO,
        await_promise=True,
    )

    if isinstance(respuesta, str):
        respuesta = json.loads(respuesta)

    if not isinstance(respuesta, dict):
        raise RuntimeError(
            "SUNARP devolvió una respuesta inesperada al leer los resultados."
        )

    return respuesta


async def ejecutar_busqueda_y_verificar(
    page: uc.Tab,
    numero_partida: str,
    timeout_segundos: float = 30.0,
) -> ResultadoBusqueda:
    numero = str(numero_partida or "").strip()

    if not numero:
        raise ValueError("El número de partida está vacío.")

    if timeout_segundos <= 0:
        raise ValueError("El tiempo de espera debe ser mayor que cero.")

    logger.info(
        "Preparando búsqueda SUNARP para la partida %s.",
        numero,
    )

    try:
        boton = await page.select("button.btn-buscar-partida")

        if not boton:
            return ResultadoBusqueda(
                estado=EstadoBusqueda.ERROR_BUSQUEDA,
                partida_solicitada=numero,
                mensaje="No se encontró el botón de búsqueda.",
            )

        await page.evaluate(
            _JS_INSTALAR_OBSERVADOR,
            await_promise=True,
        )

        await boton.click()

        logger.info(
            "Se pulsó el botón Buscar. Esperando respuesta de SUNARP."
        )

        inicio = asyncio.get_running_loop().time()
        ultima_firma = None
        lecturas_estables = 0
        hubo_mutaciones = False
        estado_final = None

        while (
            asyncio.get_running_loop().time() - inicio
            < timeout_segundos
        ):
            estado = await _leer_estado(page)

            if estado.get("mutaciones", 0) > 0:
                hubo_mutaciones = True

            firma = json.dumps(
                {
                    "existe": estado.get("existe"),
                    "titulo": estado.get("titulo"),
                    "registros": estado.get("registros"),
                    "sinDatos": estado.get("sinDatos"),
                    "spinner": estado.get("spinner"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )

            respuesta_visible = (
                bool(estado.get("titulo"))
                or bool(estado.get("registros"))
                or bool(estado.get("sinDatos"))
            )

            if (
                hubo_mutaciones
                and respuesta_visible
                and not estado.get("spinner")
            ):
                if firma == ultima_firma:
                    lecturas_estables += 1
                else:
                    ultima_firma = firma
                    lecturas_estables = 1

                if lecturas_estables >= 3:
                    estado_final = estado
                    break
            else:
                ultima_firma = firma
                lecturas_estables = 0

            await asyncio.sleep(0.5)

        if estado_final is None:
            logger.warning(
                "La búsqueda no alcanzó un estado estable en %.1f segundos.",
                timeout_segundos,
            )

            return ResultadoBusqueda(
                estado=EstadoBusqueda.TIEMPO_AGOTADO,
                partida_solicitada=numero,
                mensaje=(
                    "No se pudo confirmar la finalización de la búsqueda "
                    "dentro del tiempo de espera."
                ),
            )

        registros = estado_final.get("registros", [])

        if not registros:
            if estado_final.get("sinDatos"):
                return ResultadoBusqueda(
                    estado=EstadoBusqueda.SIN_REGISTROS,
                    partida_solicitada=numero,
                    mensaje=estado_final.get("titulo", "SUNARP no encontró registros."),
                )

            return ResultadoBusqueda(
                estado=EstadoBusqueda.ERROR_BUSQUEDA,
                partida_solicitada=numero,
                mensaje=(
                    "La respuesta se estabilizó, pero no contiene filas "
                    "ni una confirmación inequívoca de ausencia de registros."
                ),
            )

        partida_encontrada = any(
            _normalizar_partida(registro.get("partida"))
            == _normalizar_partida(numero)
            for registro in registros
        )

        resultado = ResultadoBusqueda(
            estado=EstadoBusqueda.REGISTROS_ENCONTRADOS,
            partida_solicitada=numero,
            partida_encontrada=partida_encontrada,
            registros=registros,
            mensaje=(
                "La partida solicitada aparece en los resultados."
                if partida_encontrada
                else (
                    "SUNARP devolvió registros, pero ninguno coincide "
                    "exactamente con la partida solicitada."
                )
            ),
        )

        if partida_encontrada:
            logger.info(
                "La partida %s fue localizada y verificada.",
                numero,
            )
        else:
            logger.warning(
                "Se recibieron registros, pero no se confirmó la partida %s.",
                numero,
            )

        return resultado

    except Exception as exc:
        logger.exception(
            "Error durante la búsqueda de la partida %s.",
            numero,
        )

        return ResultadoBusqueda(
            estado=EstadoBusqueda.ERROR_BUSQUEDA,
            partida_solicitada=numero,
            mensaje=str(exc),
        )

    finally:
        try:
            await page.evaluate(
                _JS_RETIRAR_OBSERVADOR,
                await_promise=True,
            )
        except Exception:
            logger.debug(
                "No fue posible retirar el observador del DOM.",
                exc_info=True,
            )
