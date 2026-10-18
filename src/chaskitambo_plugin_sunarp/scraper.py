
# Archivo:
# D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\scraper.py

from __future__ import annotations

import logging
import re
from typing import Any, AsyncGenerator

from chaskitambo.core.base_scraper import BaseScraper
from chaskitambo.core.contracts import ChaskiDocument

from .acciones.pre_extraer_partida.iniciar_session import (
    SunarpSession,
    cerrar_navegador,
    iniciar_sesion_sunarp,
)
from .acciones.area import seleccionar_area
from .acciones.oficina import seleccionar_oficina
from .acciones.criterio import configurar_criterio_partida
from .acciones.buscar import (
    EstadoBusqueda,
    ejecutar_busqueda_y_verificar,
)
from .acciones.registrar_partida_sin_detalle import (
    registrar_partida_sin_detalle,
)
from .acciones.visualizar_partida import visualizar_partida
from .acciones.extraer_asientos import extraer_asientos
from .acciones.seleccionar_pagina_asiento import seleccionar_pagina_asiento
from .acciones.esperar_pdf_listo import (
    preparar_espera_pdf,
    esperar_pdf_listo,
)
from .acciones.extraer_bytes_pdf import extraer_bytes_pdf
from .acciones.registrar_fallo_asiento import registrar_fallo_asiento

logger = logging.getLogger("chaskitambo.plugin.sunarp")


def _nombre_seguro(valor: Any, predeterminado: str) -> str:
    """Convierte un valor en un componente seguro para un nombre de archivo."""
    texto = str(valor or "").strip()
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", texto)
    texto = re.sub(r"\s+", "_", texto)
    texto = texto.strip(" ._")
    return texto or predeterminado


def _registrar_fallo(
    partida: str,
    asiento: str,
    pagina: str,
    motivo: str,
    expediente: str | None,
) -> None:
    """Registra una incidencia sin ocultar fallos de persistencia."""
    try:
        registrar_fallo_asiento(
            partida_registral=partida,
            asiento=asiento,
            pagina=pagina,
            motivo=motivo,
            expediente=expediente,
        )
    except Exception:
        logger.exception(
            "FALLO CRÍTICO DE REGISTRO: no se pudo persistir la incidencia. "
            "Partida=%s; asiento=%s; página=%s; motivo=%s",
            partida,
            asiento,
            pagina,
            motivo,
        )


class SunarpScraper(BaseScraper):
    """Scraper SUNARP con extracción de asientos y emisión de PDF."""

    def __init__(
        self,
        partida_objetivo: Any = None,
        expediente: Any = None,
        modo_local: bool = False,
        oficina: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.partida_objetivo = partida_objetivo
        self.expediente = expediente
        self.modo_local = modo_local
        self.oficina = oficina
        self.opciones_adicionales = kwargs

    async def extract(self) -> AsyncGenerator[ChaskiDocument, None]:
        logger.info(
            "Iniciando plugin SUNARP. modo_local=%s",
            self.modo_local,
        )

        if not isinstance(self.oficina, str) or not self.oficina.strip():
            logger.error("No se indicó una oficina registral válida.")
            return

        if self.partida_objetivo is None:
            logger.error("No se indicó 'partida_objetivo'.")
            return

        partida = str(self.partida_objetivo).strip()
        if not partida:
            logger.error("El número de partida está vacío.")
            return

        expediente = (
            str(self.expediente).strip()
            if self.expediente is not None and str(self.expediente).strip()
            else None
        )

        resultado = await iniciar_sesion_sunarp()
        if not isinstance(resultado, SunarpSession):
            logger.warning("No se obtuvo una sesión autenticada de SUNARP.")
            return

        sesion = resultado

        try:
            oficina = self.oficina.strip()
            logger.info("Seleccionando oficina registral: %s", oficina)

            if not await seleccionar_oficina(sesion.page, oficina):
                logger.error("No se pudo seleccionar la oficina: %s", oficina)
                return

            nombre_area = "PROPIEDAD INMUEBLE PREDIAL"
            if not await seleccionar_area(sesion.page, nombre_area):
                logger.error("No se pudo seleccionar el área: %s", nombre_area)
                return

            await configurar_criterio_partida(sesion.page, partida)

            busqueda = await ejecutar_busqueda_y_verificar(
                sesion.page,
                partida,
            )

            logger.info(
                "Resultado de búsqueda SUNARP: estado=%s, partida=%s, "
                "total=%s, coincidente=%s, mensaje=%s",
                busqueda.estado.value,
                busqueda.partida_solicitada,
                busqueda.total_registros,
                busqueda.partida_encontrada,
                busqueda.mensaje,
            )

            if busqueda.estado == EstadoBusqueda.SIN_REGISTROS:
                try:
                    registrar_partida_sin_detalle(
                        partida_registral=partida,
                        expediente=expediente,
                    )
                except Exception:
                    logger.exception(
                        "No se pudo registrar la partida sin resultados: %s",
                        partida,
                    )
                return

            if busqueda.estado == EstadoBusqueda.TIEMPO_AGOTADO:
                logger.error("Tiempo agotado en la búsqueda de %s.", partida)
                return

            if busqueda.estado == EstadoBusqueda.ERROR_BUSQUEDA:
                logger.error(
                    "Error de búsqueda para %s: %s",
                    partida,
                    busqueda.mensaje,
                )
                return

            if (
                busqueda.estado != EstadoBusqueda.REGISTROS_ENCONTRADOS
                or not busqueda.partida_encontrada
            ):
                logger.error(
                    "La búsqueda no confirmó la partida %s.",
                    partida,
                )
                return

            visualizacion = await visualizar_partida(
                sesion.page,
                partida,
            )

            if not visualizacion.get("ok"):
                logger.error(
                    "No se confirmó la visualización de la partida %s: %s",
                    partida,
                    visualizacion,
                )
                return

            resultado_asientos = await extraer_asientos(
                sesion.page,
                partida,
            )

            if resultado_asientos is None:
                logger.error(
                    "No se pudieron extraer los asientos de %s.",
                    partida,
                )
                return

            asientos = resultado_asientos.get("asientos")
            if not isinstance(asientos, list):
                logger.error(
                    "La lista de asientos no tiene un formato válido: %s",
                    partida,
                )
                return

            logger.info(
                "Partida %s: %s asientos para procesar.",
                partida,
                len(asientos),
            )

            documentos_emitidos = 0
            paginas_procesadas = 0
            paginas_fallidas = 0

            for datos_asiento in asientos:
                if not isinstance(datos_asiento, dict):
                    logger.error(
                        "Registro de asiento inválido en la partida %s: %r",
                        partida,
                        datos_asiento,
                    )
                    continue

                numero_asiento = str(
                    datos_asiento.get("asiento", "")
                ).strip()

                paginas = datos_asiento.get("paginas")

                if not numero_asiento:
                    logger.error(
                        "Se encontró un asiento sin número en la partida %s.",
                        partida,
                    )
                    continue

                if not isinstance(paginas, list) or not paginas:
                    motivo = "El asiento no contiene una lista válida de páginas."
                    _registrar_fallo(
                        partida,
                        numero_asiento,
                        "desconocida",
                        motivo,
                        expediente,
                    )
                    paginas_fallidas += 1
                    continue

                for valor_pagina in paginas:
                    numero_pagina = str(valor_pagina or "").strip()

                    if not numero_pagina:
                        numero_pagina = "desconocida"
                        motivo = "La página del asiento está vacía."
                        _registrar_fallo(
                            partida,
                            numero_asiento,
                            numero_pagina,
                            motivo,
                            expediente,
                        )
                        paginas_fallidas += 1
                        continue

                    paginas_procesadas += 1

                    try:
                        diagnostico_js = r"""
                        (() => {
                            const inspeccionarVentana = (ventana, origen) => {
                                try {
                                    const app = ventana.PDFViewerApplication;
                                    const doc = app?.pdfDocument;

                                    return {
                                        origen,
                                        accesible: true,
                                        pdfViewerApplication: !!app,
                                        documentoPDF: !!doc,
                                        getDataDisponible: typeof doc?.getData === "function",
                                        paginas: doc?.numPages ?? null,
                                        url: ventana.location.href
                                    };
                                } catch (error) {
                                    return {
                                        origen,
                                        accesible: false,
                                        error: String(error)
                                    };
                                }
                            };

                            const resultado = {
                                ventanaPrincipal: inspeccionarVentana(window, "principal"),
                                iframes: []
                            };

                            document.querySelectorAll("iframe").forEach((iframe, indice) => {
                                let entrada = {
                                    indice,
                                    src: iframe.getAttribute("src"),
                                    title: iframe.title || null
                                };

                                try {
                                    const ventana = iframe.contentWindow;

                                    if (ventana) {
                                        entrada = {
                                            ...entrada,
                                            ...inspeccionarVentana(ventana, `iframe[${indice}]`)
                                        };
                                    }
                                } catch (error) {
                                    entrada.accesoError = String(error);
                                }

                                resultado.iframes.push(entrada);
                            });

                            return JSON.stringify(resultado);
                        })()
                        """

                        resultado = await sesion.page.evaluate(
                            diagnostico_js,
                            await_promise=True,
                        )

                        print("[DIAGNÓSTICO PDF.JS]")
                        print(resultado)
                        # Instalar la observación antes del clic.
                        await preparar_espera_pdf(sesion.page)

                        seleccionada = await seleccionar_pagina_asiento(
                            sesion.page,
                            numero_asiento=numero_asiento,
                            numero_pagina=numero_pagina,
                        )

                        if not seleccionada:
                            raise RuntimeError(
                                "No se pudo seleccionar la página del asiento."
                            )

                        await esperar_pdf_listo(sesion.page)

                        pdf_bytes = await extraer_bytes_pdf(sesion.page)

                        if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
                            raise ValueError(
                                "La extracción devolvió bytes vacíos o inválidos."
                            )

                        if not pdf_bytes.startswith(b"%PDF-"):
                            raise ValueError(
                                "El contenido extraído no tiene cabecera PDF."
                            )

                        if b"%%EOF" not in pdf_bytes[-2048:]:
                            raise ValueError(
                                "El PDF no contiene el marcador final %%EOF."
                            )

                        id_externo = (
                            f"{partida}-asiento-{numero_asiento}"
                            f"-pagina-{numero_pagina}"
                        )

                        nombre_archivo = (
                            f"SUNARP_{_nombre_seguro(partida, 'partida')}"
                            f"_asiento_{_nombre_seguro(numero_asiento, 'sin_numero')}"
                            f"_pagina_{_nombre_seguro(numero_pagina, 'sin_numero')}.pdf"
                        )

                        documento = ChaskiDocument(
                            id_externo=id_externo,
                            fuente="sunarp",
                            pdf_bytes=pdf_bytes,
                            nombre_archivo_sugerido=nombre_archivo,
                            metadatos={
                                "partida_registral": partida,
                                "expediente": expediente,
                                "oficina_registral": oficina,
                                "area_registral": nombre_area,
                                "asiento": numero_asiento,
                                "acto": datos_asiento.get("acto"),
                                "anio": datos_asiento.get("anio"),
                                "rubro": datos_asiento.get("rubro"),
                                "pagina": numero_pagina,
                                "descripcion": datos_asiento.get("descripcion"),
                                "validacion_pdf": "ok",
                            },
                        )

                        logger.info(
                            "PDF válido. Partida=%s; asiento=%s; página=%s; "
                            "bytes=%s. Emitiendo ChaskiDocument.",
                            partida,
                            numero_asiento,
                            numero_pagina,
                            len(pdf_bytes),
                        )

                        documentos_emitidos += 1
                        yield documento

                    except Exception as exc:
                        paginas_fallidas += 1
                        motivo = f"{type(exc).__name__}: {exc}"

                        logger.exception(
                            "Falló la página del asiento. Se intentará "
                            "continuar con la siguiente. Partida=%s; "
                            "asiento=%s; página=%s.",
                            partida,
                            numero_asiento,
                            numero_pagina,
                        )

                        _registrar_fallo(
                            partida,
                            numero_asiento,
                            numero_pagina,
                            motivo,
                            expediente,
                        )

                        # No se emite ningún documento para esta página.
                        continue

            logger.info(
                "Procesamiento de partida finalizado. Partida=%s; "
                "páginas intentadas=%s; documentos emitidos=%s; "
                "páginas fallidas=%s.",
                partida,
                paginas_procesadas,
                documentos_emitidos,
                paginas_fallidas,
            )

        except Exception:
            logger.exception(
                "Error global durante el procesamiento SUNARP de la partida %s.",
                partida,
            )
            raise

        finally:
            logger.info("Cerrando el navegador de la sesión SUNARP.")
            try:
                await cerrar_navegador(sesion.browser)
            except Exception:
                logger.exception(
                    "No se pudo cerrar limpiamente el navegador SUNARP."
                )
