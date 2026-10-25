from chaskitambo_plugin_sunarp.acciones import pre_extraer_partida
import logging
import re
from typing import Any, AsyncGenerator
import json
from pathlib import Path
import os
import uuid
from chaskitambo.core.base_scraper import BaseScraper
from chaskitambo.core.contracts import ChaskiDocument
from datetime import datetime
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
from .acciones.regresar_a_busqueda import regresar_a_busqueda
from websockets.exceptions import ConnectionClosed
from chaskitambo_plugin_sunarp.manifest import SunarpManifest
from .acciones.sanitizacion import obtener_nombre_oficina


logger = logging.getLogger("chaskitambo.plugin.sunarp")

AREA_REGISTRAL = "PROPIEDAD INMUEBLE PREDIAL"


def _nombre_seguro(valor: Any, predeterminado: str) -> str:
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
    registrar_incidencia=None,
) -> bool:
    try:
        registrar_fallo_asiento(
            partida_registral=partida,
            asiento=asiento,
            pagina=pagina,
            motivo=motivo,
            expediente=expediente,
        )
        return True
    except Exception as exc:
        logger.exception(
            "FALLO CRÍTICO DE REGISTRO: no se pudo persistir la incidencia. "
            "Partida=%s; asiento=%s; página=%s; motivo=%s",
            partida,
            asiento,
            pagina,
            motivo,
        )

        if registrar_incidencia is not None:
            registrar_incidencia(
                f"No se pudo persistir la incidencia original: {exc}",
                asiento=asiento,
                pagina=pagina,
                etapa="persistencia_incidencia",
                persistencia="fallida",
            )

        return False
def _proceso_sigue_activo(pid: Any) -> bool | None:
    """
    Comprueba si un PID sigue existiendo.

    Retorna:
        True: el proceso existe o no se puede comprobar por permisos.
        False: el sistema confirma que el PID no existe.
        None: el resultado es indeterminado.

    No demuestra por sí sola que un PID existente pertenezca a la
    ejecución SUNARP original. Por seguridad, un PID existente bloquea
    la recuperación automática.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # El proceso podría existir, aunque no podamos inspeccionarlo.
        return True
    except OSError as exc:
        # En Windows, ciertos códigos indican que el PID no existe.
        if getattr(exc, "winerror", None) == 87:
            return False

        logger.warning(
            "No se pudo comprobar el PID %s: %s",
            pid,
            exc,
        )
        return None

def _es_error_conexion_nodriver(exc: BaseException) -> bool:
    """Detecta errores de conexión incluso si están envueltos."""

    pendientes = [exc]
    visitadas: set[int] = set()

    while pendientes:
        actual = pendientes.pop()

        if id(actual) in visitadas:
            continue

        visitadas.add(id(actual))

        nombre = type(actual).__name__
        modulo = type(actual).__module__
        mensaje = str(actual).lower()

        if (
            nombre in {
                "ConnectionClosed",
                "ConnectionClosedError",
                "ConnectionClosedOK",
            }
            and (
                modulo.startswith("websockets")
                or modulo.startswith("websocket")
            )
        ):
            return True

        if (
            "connectionclosederror" in mensaje
            or "no close frame received or sent" in mensaje
        ):
            return True

        if actual.__cause__ is not None:
            pendientes.append(actual.__cause__)

        if actual.__context__ is not None:
            pendientes.append(actual.__context__)

    return False


class SunarpScraper(BaseScraper):
    
    def __init__(
        self,
        partida_objetivo: Any = None,
        expediente: Any = None,
        modo_local: bool = False,
        oficina: str | None = None,
        partidas_objetivo: Any = None,
        archivo_json: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        self.partida_objetivo = partida_objetivo
        self.expediente = expediente
        self.modo_local = modo_local
        self.oficina = oficina
        self.archivo_json = (
            Path(archivo_json).expanduser()
            if archivo_json is not None
            else None
        )
        self.archivo_busqueda_manual = (
            self.archivo_json.with_name("busqueda_manual_sunarp.json")
            if self.archivo_json is not None
            else Path.cwd() / "busqueda_manual_sunarp.json"
        )
        self.opciones_adicionales = kwargs
        self.manifest = SunarpManifest()
        self.ejecucion_id = uuid.uuid4().hex
        self.pid_proceso = os.getpid()

        # 1. Recoger las partidas indicadas directamente.
        entradas: list[Any] = []

        if partidas_objetivo is not None:
            if isinstance(partidas_objetivo, (str, int)):
                entradas.append(partidas_objetivo)
            else:
                entradas.extend(list(partidas_objetivo))

        elif partida_objetivo is not None:
            if isinstance(partida_objetivo, (str, int)):
                entradas.append(partida_objetivo)
            else:
                entradas.extend(list(partida_objetivo))

        # 2. Incorporar las partidas procedentes del JSON de REMAJU.
        expedientes_json: dict[str, str] = {}
        oficinas_json: dict[str, str] = {}

        if self.archivo_json is not None:
            (
                partidas_json,
                expedientes_json,
                oficinas_json,
            ) = self._leer_partidas_json(self.archivo_json)

            entradas.extend(partidas_json)

        # 3. Eliminar valores vacíos y duplicados conservando el orden.
        self.partidas_objetivo: list[str] = []
        partidas_vistas: set[str] = set()

        for valor in entradas:
            if valor is None:
                continue

            partida = str(valor).strip()

            if not partida or partida in partidas_vistas:
                continue

            partidas_vistas.add(partida)
            self.partidas_objetivo.append(partida)

        # 4. Combinar los expedientes del JSON con los configurados
        # explícitamente. La configuración explícita tiene prioridad.
        expedientes_configurados = self.opciones_adicionales.get(
            "expedientes_por_partida",
            {},
        )
        if not isinstance(expedientes_configurados, dict):
            raise ValueError(
                "'expedientes_por_partida' debe ser un diccionario."
            )

        expedientes_combinados = dict(expedientes_json)
        expedientes_combinados.update(expedientes_configurados)
        self.opciones_adicionales["expedientes_por_partida"] = (
            expedientes_combinados
        )

        oficinas_configuradas = self.opciones_adicionales.get(
            "oficinas_por_partida",
            {},
        )
        if not isinstance(oficinas_configuradas, dict):
            raise ValueError(
                "'oficinas_por_partida' debe ser un diccionario."
            )

        oficinas_combinadas = dict(oficinas_json)
        oficinas_combinadas.update(oficinas_configuradas)
        self.opciones_adicionales["oficinas_por_partida"] = (
            oficinas_combinadas
        )

        # 5. Inicializar los reportes.
        self.reportes_partidas: list[dict[str, Any]] = []

        self.execution_report: dict[str, Any] = {
            "estado": "pendiente",
            "partida_registral": (
                str(partida_objetivo).strip()
                if isinstance(partida_objetivo, (str, int))
                else None
            ),
            "expediente": (
                str(expediente).strip()
                if expediente is not None
                else None
            ),
            "oficina_registral": oficina,
            "archivo_json": (
                str(self.archivo_json)
                if self.archivo_json is not None
                else None
            ),
            "partidas_previstas": len(self.partidas_objetivo),
            "partidas_completadas": 0,
            "partidas_incompletas": 0,
            "partidas_revision_manual": 0,
            "partidas_no_procesadas": 0,
            "partidas_fallidas": 0,
            "partidas_sin_registros": 0,
            "paginas_previstas": 0,
            "paginas_intentadas": 0,
            "paginas_exitosas": 0,
            "paginas_fallidas": 0,
            "asientos_previstos": 0,
            "asientos_fallidos": 0,
            "documentos_emitidos": 0,
            "incidencias": [],
            "reportes_partidas": self.reportes_partidas,
            "error": None,
        }


    
    @staticmethod
    def _leer_partidas_json(
        archivo_json: str | Path,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        """Lee partidas, expedientes y oficinas del JSON de REMAJU."""
        ruta = Path(archivo_json).expanduser()

        if not ruta.is_file():
            raise FileNotFoundError(
                f"No existe el archivo JSON de REMAJU: {ruta}"
            )

        try:
            with ruta.open("r", encoding="utf-8-sig") as archivo:
                contenido = json.load(archivo)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON inválido: línea {exc.lineno}, "
                f"columna {exc.colno}."
            ) from exc

        if not isinstance(contenido, dict):
            raise ValueError("La raíz del JSON debe ser un objeto.")

        registros = contenido.get("registros")

        if not isinstance(registros, list):
            raise ValueError(
                "El JSON debe contener una lista llamada 'registros'."
            )

        partidas: list[str] = []
        partidas_vistas: set[str] = set()
        expedientes_por_partida: dict[str, str] = {}
        oficinas_por_partida: dict[str, str] = {}

        for indice, registro in enumerate(registros):
            if not isinstance(registro, dict):
                logger.warning(
                    "Se omite el registro %s: formato inválido.",
                    indice,
                )
                continue

            datos = registro.get("datos")
            if not isinstance(datos, dict):
                logger.warning(
                    "Se omite el registro %s: falta 'datos'.",
                    indice,
                )
                continue

            remate = datos.get("remate")
            inmuebles = datos.get("inmuebles")

            if not isinstance(remate, dict):
                logger.warning(
                    "Se omite el registro %s: falta 'datos.remate'.",
                    indice,
                )
                continue

            if not isinstance(inmuebles, list):
                logger.warning(
                    "Se omite el registro %s: 'inmuebles' no es una lista.",
                    indice,
                )
                continue

            expediente_valor = (
                registro.get("expediente")
                or remate.get("expediente")
            )
            expediente = (
                str(expediente_valor).strip()
                if expediente_valor is not None
                else ""
            )

            oficina = obtener_nombre_oficina(expediente) or ""

            for inmueble in inmuebles:
                if not isinstance(inmueble, dict):
                    continue

                valor_partida = inmueble.get("partidaRegistral")
                if valor_partida is None:
                    continue

                partida = str(valor_partida).strip()
                if not partida:
                    continue

                if partida in partidas_vistas:
                    # No sobrescribir silenciosamente las asociaciones.
                    if (
                        expediente
                        and expedientes_por_partida.get(partida)
                        and expedientes_por_partida[partida] != expediente
                    ):
                        logger.warning(
                            "La partida %s aparece en varios expedientes. "
                            "Se conserva la primera asociación.",
                            partida,
                        )

                    if (
                        oficina
                        and oficinas_por_partida.get(partida)
                        and oficinas_por_partida[partida] != oficina
                    ):
                        logger.warning(
                            "La partida %s aparece con varias oficinas. "
                            "Se conserva la primera asociación.",
                            partida,
                        )
                    continue

                partidas_vistas.add(partida)
                partidas.append(partida)

                if expediente:
                    expedientes_por_partida[partida] = expediente

                if oficina:
                    oficinas_por_partida[partida] = oficina

        logger.info(
            "JSON REMAJU leído: registros=%s; partidas=%s; "
            "con expediente=%s; con oficina=%s.",
            len(registros),
            len(partidas),
            len(expedientes_por_partida),
            len(oficinas_por_partida),
        )

        return partidas, expedientes_por_partida, oficinas_por_partida

    def _registrar_busqueda_manual(
        self,
        partida: str,
        expediente: str | None,
        oficina: str | None,
    ) -> None:
        """
        Registra una búsqueda SUNARP ejecutada correctamente sin registros.

        Conserva el historial existente, evita duplicados por partida y
        expediente y utiliza escritura atómica para evitar archivos parciales.

        Si el archivo existente no es válido o no puede guardarse, lanza
        una excepción para que la partida no se marque como completada.
        """
        ruta = self.archivo_busqueda_manual
        ruta.parent.mkdir(parents=True, exist_ok=True)

        if ruta.exists():
            try:
                with ruta.open("r", encoding="utf-8-sig") as archivo:
                    datos = json.load(archivo)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"No se pudo leer el historial de búsqueda manual: {ruta}"
                ) from exc

            if not isinstance(datos, dict):
                raise ValueError(
                    f"El archivo de búsqueda manual debe contener un objeto JSON: {ruta}"
                )

            registros = datos.get("registros")

            if not isinstance(registros, list):
                raise ValueError(
                    f"El campo 'registros' debe ser una lista: {ruta}"
                )

            version = datos.get("version", 1)
            fuente = datos.get("fuente", "sunarp")

            if version != 1 or fuente != "sunarp":
                raise ValueError(
                    f"Versión o fuente incompatible en el archivo: {ruta}"
                )
        else:
            datos = {
                "version": 1,
                "fuente": "sunarp",
                "registros": [],
            }
            registros = datos["registros"]

        partida_normalizada = str(partida).strip()
        expediente_normalizado = str(expediente or "").strip()

        # No duplicar una partida para el mismo expediente.
        for registro in registros:
            if not isinstance(registro, dict):
                raise ValueError(
                    f"El historial contiene un registro inválido: {ruta}"
                )

            partida_existente = str(
                registro.get("partida_registral", "")
            ).strip()

            expediente_existente = str(
                registro.get("expediente") or ""
            ).strip()

            if (
                partida_existente == partida_normalizada
                and expediente_existente == expediente_normalizado
            ):
                logger.info(
                    "La búsqueda manual ya está registrada: partida=%s, expediente=%s",
                    partida_normalizada,
                    expediente_normalizado or None,
                )
                return

        registros.append(
            {
                "partida_registral": partida_normalizada,
                "expediente": expediente_normalizado or None,
                "oficina_registral": oficina,
                "resultado_busqueda": "sin_registros",
                "requiere_revision_manual": True,
                "motivo": (
                    "La búsqueda SUNARP finalizó correctamente, "
                    "pero no encontró registros para la partida solicitada."
                ),
                "fecha": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
            }
        )

        # Escritura temporal en el mismo directorio y sustitución atómica.
        ruta_temporal = ruta.with_name(f"{ruta.name}.tmp")

        try:
            with ruta_temporal.open("w", encoding="utf-8") as archivo:
                json.dump(
                    datos,
                    archivo,
                    ensure_ascii=False,
                    indent=2,
                )
                archivo.write("\n")
                archivo.flush()

            ruta_temporal.replace(ruta)

        except Exception:
            try:
                ruta_temporal.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "No se pudo eliminar el archivo temporal %s",
                    ruta_temporal,
                )
            raise

        logger.info(
            "Búsqueda manual registrada: partida=%s, expediente=%s, archivo=%s",
            partida_normalizada,
            expediente_normalizado or None,
            ruta,
        )


    def _registrar_incidencia(
        self,
        motivo: str,
        *,
        asiento: str | None = None,
        pagina: str | None = None,
        etapa: str = "extraccion",
        persistencia: str = "no_aplicable",
    ) -> None:
        self.execution_report["incidencias"].append(
            {
                "etapa": etapa,
                "partida_registral": self.execution_report.get(
                    "partida_registral"
                ),
                "expediente": self.execution_report.get("expediente"),
                "asiento": asiento,
                "pagina": pagina,
                "motivo": motivo,
                "persistencia_incidencia": persistencia,
            }
        )

    def _finalizar_reporte(
        self,
        estado: str,
        error: str | None = None,
    ) -> None:
        self.execution_report["estado"] = estado

        if error:
            self.execution_report["error"] = error

        logger.info(
            "Reporte SUNARP: partida=%s; estado=%s; previstas=%s; "
            "intentadas=%s; exitosas=%s; fallidas=%s; emitidos=%s.",
            self.execution_report.get("partida_registral"),
            estado,
            self.execution_report.get("paginas_previstas", 0),
            self.execution_report.get("paginas_intentadas", 0),
            self.execution_report.get("paginas_exitosas", 0),
            self.execution_report.get("paginas_fallidas", 0),
            self.execution_report.get("documentos_emitidos", 0),
        )

    def _crear_reporte_partida(
        self,
        partida: str,
        expediente: str | None,
        oficina: str,
    ) -> dict[str, Any]:
        return {
            "estado": "en_ejecucion",
            "partida_registral": partida,
            "expediente": expediente,
            "oficina_registral": oficina,
            "resultado_busqueda": None,
            "requiere_revision_manual": False,
            "paginas_previstas": 0,
            "paginas_intentadas": 0,
            "paginas_exitosas": 0,
            "paginas_fallidas": 0,
            "asientos_previstos": 0,
            "asientos_fallidos": 0,
            "documentos_emitidos": 0,
            "incidencias": [],
            "error": None,
        }

    async def _procesar_partida_impl(
        self,
        sesion: SunarpSession,
        partida: str,
        expediente: str | None,
        oficina: str,
    ) -> AsyncGenerator[ChaskiDocument, None]:
        """
        Procesa una partida utilizando una sesión ya autenticada.

        Este método no inicia sesión ni cierra el navegador.
        """
        page = sesion.page
        reporte = self.execution_report

        logger.info(
            "Procesando partida=%s; expediente=%s; oficina=%s",
            partida,
            expediente,
            oficina,
        )

        logger.info("Seleccionando oficina registral: %s", oficina)
        clave_manifest = self.manifest.registrar_partida(
            oficina=oficina,
            area=AREA_REGISTRAL,
            partida=partida,
            expediente=expediente or "",
        )

        if not await seleccionar_oficina(page, oficina):
            motivo = (
                "No se pudo seleccionar la oficina registral. "
                "La causa puede ser una coincidencia inexistente o un "
                "problema técnico durante la selección."
            )

            reporte["requiere_revision_manual"] = True

            self._registrar_incidencia(
                motivo,
                etapa="seleccion_oficina",
            )

            self._finalizar_reporte("revision_manual", motivo)
            return

        if not await seleccionar_area(page, AREA_REGISTRAL):
            motivo = f"No se pudo seleccionar el área: {AREA_REGISTRAL}"
            self._registrar_incidencia(
                motivo,
                etapa="seleccion_area",
            )
            self._finalizar_reporte("fallida", motivo)
            return

        await configurar_criterio_partida(page, partida)
        busqueda = await ejecutar_busqueda_y_verificar(page, partida)

        logger.info(
            "Resultado de búsqueda SUNARP: estado=%s; partida=%s; "
            "total=%s; coincidente=%s; mensaje=%s",
            busqueda.estado.value,
            busqueda.partida_solicitada,
            busqueda.total_registros,
            busqueda.partida_encontrada,
            busqueda.mensaje,
        )

        if busqueda.estado == EstadoBusqueda.SIN_REGISTROS:
            reporte["resultado_busqueda"] = "sin_registros"
            reporte["requiere_revision_manual"] = True

            errores_persistencia = []

            # 1. Conservar la persistencia que ya utiliza el plugin.
            try:
                await registrar_partida_sin_detalle(
                    partida_registral=partida,
                    expediente=expediente,
                )
            except Exception as exc:
                motivo = (
                    "Falló la persistencia de la partida sin detalle: "
                    f"{type(exc).__name__}: {exc}"
                )
                errores_persistencia.append(motivo)

                self._registrar_incidencia(
                    motivo=motivo,
                    etapa="persistencia_sin_detalle",
                    persistencia="fallida",
                )

                logger.exception(
                    "No se pudo registrar la partida sin detalle: %s",
                    partida,
                )

            # 2. Guardar siempre la traza acumulativa para revisión manual.
            try:
                self._registrar_busqueda_manual(
                    partida=partida,
                    expediente=expediente,
                    oficina=oficina,
                )
            except Exception as exc:
                motivo = (
                    "Falló el registro en busqueda_manual_sunarp.json: "
                    f"{type(exc).__name__}: {exc}"
                )
                errores_persistencia.append(motivo)

                self._registrar_incidencia(
                    motivo=motivo,
                    etapa="persistencia_busqueda_manual",
                    persistencia="fallida",
                )

                logger.exception(
                    "No se pudo guardar la búsqueda manual de la partida %s",
                    partida,
                )

            # 3. El resultado de búsqueda se distingue del estado de procesamiento.
            if errores_persistencia:
                reporte["error"] = "; ".join(errores_persistencia)
                self._finalizar_reporte("incompleta", reporte["error"])
            else:
                self._finalizar_reporte("completada")

            return

        if busqueda.estado == EstadoBusqueda.TIEMPO_AGOTADO:
            motivo = f"Tiempo agotado en la búsqueda de la partida {partida}."
            self._registrar_incidencia(motivo, etapa="busqueda")
            self._finalizar_reporte("fallida", motivo)
            return

        if busqueda.estado == EstadoBusqueda.ERROR_BUSQUEDA:
            motivo = f"Error de búsqueda: {busqueda.mensaje}"
            self._registrar_incidencia(motivo, etapa="busqueda")
            self._finalizar_reporte("fallida", motivo)
            return

        if (
            busqueda.estado != EstadoBusqueda.REGISTROS_ENCONTRADOS
            or not busqueda.partida_encontrada
        ):
            motivo = f"No se confirmó la partida registral {partida}."
            self._registrar_incidencia(
                motivo,
                etapa="validacion_busqueda",
            )
            self._finalizar_reporte("fallida", motivo)
            return
        reporte["resultado_busqueda"] = "registros_encontrados"

        visualizacion = await visualizar_partida(page, partida)

        if not visualizacion.get("ok"):
            motivo = (
                f"No se confirmó la visualización de la partida "
                f"{partida}: {visualizacion}"
            )
            self._registrar_incidencia(
                motivo,
                etapa="validacion_visualizacion",
            )
            self._finalizar_reporte("fallida", motivo)
            return

        resultado_asientos = await extraer_asientos(page, partida)

        if resultado_asientos is None:
            motivo = f"No se pudieron extraer los asientos de {partida}."
            self._registrar_incidencia(
                motivo,
                etapa="extraccion_asientos",
            )
            self._finalizar_reporte("fallida", motivo)
            return

        asientos = resultado_asientos.get("asientos")

        if not isinstance(asientos, list):
            motivo = (
                f"La lista de asientos no tiene un formato válido: "
                f"{partida}."
            )
            self._registrar_incidencia(
                motivo,
                etapa="validacion_asientos",
            )
            self._finalizar_reporte("fallida", motivo)
            return

        logger.info(
            "Partida %s: %s asientos para procesar.",
            partida,
            len(asientos),
        )

        reporte["asientos_previstos"] = len(asientos)
        reporte["paginas_previstas"] = sum(
            len(asiento["paginas"])
            for asiento in asientos
            if (
                isinstance(asiento, dict)
                and isinstance(asiento.get("paginas"), list)
            )
        )

        for datos_asiento in asientos:
            if not isinstance(datos_asiento, dict):
                motivo = (
                    "Se encontró un registro de asiento que "
                    "no es un diccionario."
                )
                reporte["asientos_fallidos"] += 1
                self._registrar_incidencia(
                    motivo,
                    etapa="validacion_asiento",
                )
                logger.error(
                    "%s Partida=%s; registro=%r",
                    motivo,
                    partida,
                    datos_asiento,
                )
                continue

            numero_asiento = str(
                datos_asiento.get("asiento", "")
            ).strip()

            paginas = datos_asiento.get("paginas")

            if not numero_asiento:
                motivo = "Se encontró un asiento sin número."
                reporte["asientos_fallidos"] += 1
                self._registrar_incidencia(
                    motivo,
                    etapa="validacion_asiento",
                )
                logger.error("%s Partida=%s", motivo, partida)
                continue

            if not isinstance(paginas, list) or not paginas:
                motivo = (
                    "El asiento no contiene una lista válida de páginas."
                )
                reporte["asientos_fallidos"] += 1
                self._registrar_incidencia(
                    motivo,
                    asiento=numero_asiento,
                    etapa="validacion_paginas",
                )
                _registrar_fallo(
                    partida,
                    numero_asiento,
                    "desconocida",
                    motivo,
                    expediente,
                    registrar_incidencia=self._registrar_incidencia,
                )
                continue

            for valor_pagina in paginas:
                numero_pagina = str(valor_pagina or "").strip()
                reporte["paginas_intentadas"] += 1

                if not numero_pagina:
                    numero_pagina = "desconocida"
                    motivo = "La página del asiento está vacía."
                    reporte["paginas_fallidas"] += 1
                    self._registrar_incidencia(
                        motivo,
                        asiento=numero_asiento,
                        pagina=numero_pagina,
                        etapa="validacion_pagina",
                    )
                    _registrar_fallo(
                        partida,
                        numero_asiento,
                        numero_pagina,
                        motivo,
                        expediente,
                        registrar_incidencia=self._registrar_incidencia,
                    )
                    continue

                try:
                    await preparar_espera_pdf(page)

                    seleccionada = await seleccionar_pagina_asiento(
                        page,
                        numero_asiento=numero_asiento,
                        numero_pagina=numero_pagina,
                    )

                    if not seleccionada:
                        raise RuntimeError(
                            "No se pudo seleccionar la página del asiento."
                        )

                    await esperar_pdf_listo(page)
                    pdf_bytes = await extraer_bytes_pdf(page)

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
                            "area_registral": AREA_REGISTRAL,
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

                    reporte["paginas_exitosas"] += 1
                    reporte["documentos_emitidos"] += 1
                    yield documento

                except Exception as exc:
                    if _es_error_conexion_nodriver(exc):
                        logger.exception(
                            "Se perdió la conexión con Nodriver durante "
                            "la descarga. Se interrumpe la partida para "
                            "evitar continuar sobre una sesión inválida. "
                            "Partida=%s; asiento=%s; página=%s.",
                            partida,
                            numero_asiento,
                            numero_pagina,
                        )
                        raise

                    reporte["paginas_fallidas"] += 1
                    motivo = f"{type(exc).__name__}: {exc}"

                    self._registrar_incidencia(
                        motivo,
                        asiento=numero_asiento,
                        pagina=numero_pagina,
                        etapa="descarga_pdf",
                    )

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
                        registrar_incidencia=self._registrar_incidencia,
                    )

        tiene_incidencias = bool(reporte["incidencias"])
        tiene_fallos = (
            reporte["paginas_fallidas"] > 0
            or reporte["asientos_fallidos"] > 0
        )

        if tiene_fallos or tiene_incidencias:
            estado_final = (
                "incompleta"
                if reporte["paginas_exitosas"] > 0
                else "fallida"
            )
        else:
            estado_final = "completada"

        self._finalizar_reporte(estado_final)

    async def _procesar_partida(
        self,
        sesion: SunarpSession,
        partida: str,
        expediente: str | None,
        oficina: str,
    ) -> AsyncGenerator[ChaskiDocument, None]:
        """
        Controla el ciclo de vida del manifiesto y delega el procesamiento
        funcional en _procesar_partida_impl().
        """
        clave_manifest = self.manifest.registrar_partida(
            oficina=oficina,
            area=AREA_REGISTRAL,
            partida=partida,
            expediente=expediente or "",
        )

        self.manifest.iniciar_ejecucion_partida(
            clave=clave_manifest,
            ejecucion_id=self.ejecucion_id,
            pid_proceso=self.pid_proceso,
        )

        try:
            async for documento in self._procesar_partida_impl(
                sesion=sesion,
                partida=partida,
                expediente=expediente,
                oficina=oficina,
            ):
                yield documento

        except BaseException as exc:
            # Incluye errores y cancelaciones. No se oculta la excepción.
            try:
                registro = self.manifest.obtener_partida(clave_manifest)

                if (
                    registro is not None
                    and registro.get("ejecucion_id") == self.ejecucion_id
                    and registro.get("estado") == "procesando"
                ):
                    self.manifest.actualizar_partida(
                        clave_manifest,
                        "error",
                        motivo=(
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )
            except Exception:
                logger.exception(
                    "No se pudo actualizar el manifiesto tras un error. "
                    "Partida=%s",
                    partida,
                )

            raise

        finally:
            try:
                registro = self.manifest.obtener_partida(clave_manifest)

                # Solo la ejecución propietaria puede finalizar la partida.
                if (
                    registro is not None
                    and registro.get("ejecucion_id") == self.ejecucion_id
                    and registro.get("estado") == "procesando"
                ):
                    estado_reporte = self.execution_report.get("estado")

                    equivalencias = {
                        "completada": "completada",
                        "incompleta": "error",
                        "fallida": "error",
                        "revision_manual": "revision_manual",
                    }

                    estado_manifest = equivalencias.get(
                        estado_reporte,
                        "error",
                    )

                    motivo = self.execution_report.get("error")

                    if estado_manifest == "error" and not motivo:
                        motivo = (
                            "El procesamiento terminó sin un estado final "
                            "confirmado."
                        )

                    self.manifest.actualizar_partida(
                        clave_manifest,
                        estado_manifest,
                        motivo=motivo,
                    )

            except Exception:
                logger.exception(
                    "No se pudo finalizar el estado del manifiesto. "
                    "Partida=%s",
                    partida,
                )

    async def extract(self) -> AsyncGenerator[ChaskiDocument, None]:
        logger.info(
            "Iniciando plugin SUNARP. modo_local=%s; partidas=%s",
            self.modo_local,
            len(self.partidas_objetivo),
        )

        if not self.partidas_objetivo:
            self._finalizar_reporte(
                "fallida",
                "No se indicó ninguna partida registral.",
            )
            return

        oficinas_por_partida = self.opciones_adicionales.get(
            "oficinas_por_partida", {}
        )
        expedientes_por_partida = self.opciones_adicionales.get(
            "expedientes_por_partida", {}
        )

        if not isinstance(oficinas_por_partida, dict):
            self._finalizar_reporte(
                "fallida",
                "'oficinas_por_partida' debe ser un diccionario.",
            )
            return

        if not isinstance(expedientes_por_partida, dict):
            self._finalizar_reporte(
                "fallida",
                "'expedientes_por_partida' debe ser un diccionario.",
            )
            return

        reporte_lote = self.execution_report

        expediente_base = (
            str(self.expediente).strip()
            if self.expediente is not None
            and str(self.expediente).strip()
            else None
        )

        try:
            # Validar oficinas antes de iniciar el navegador.
            partidas_validadas: list[str] = []

            for partida in self.partidas_objetivo:
                expediente_valor = expedientes_por_partida.get(
                    partida,
                    expediente_base,
                )

                expediente = (
                    str(expediente_valor).strip()
                    if expediente_valor is not None
                    and str(expediente_valor).strip()
                    else None
                )

                oficina = obtener_nombre_oficina(expediente) if expediente else None

                if oficina is None:
                    motivo = (
                        "Expediente sin código de corte registrado en "
                        "MAPEO_CORTE_JUDICIAL_SUNARP."
                    )

                    reporte_partida = self._crear_reporte_partida(
                        partida,
                        expediente,
                        "",
                    )
                    reporte_partida["estado"] = "revision_manual"
                    reporte_partida["requiere_revision_manual"] = True
                    reporte_partida["error"] = motivo
                    reporte_partida["incidencias"].append({
                        "etapa": "sanitizacion_expediente",
                        "motivo": motivo,
                    })

                    self.reportes_partidas.append(reporte_partida)
                    reporte_lote["partidas_revision_manual"] += 1

                    logger.warning(
                        "Partida enviada a revisión manual; se omite sin "
                        "iniciar el navegador: partida=%s; expediente=%s.",
                        partida,
                        expediente,
                    )
                    continue

                # El código judicial determina la oficina. No usar el distrito
                # judicial de REMAJU como sustituto.
                oficinas_por_partida[partida] = oficina
                partidas_validadas.append(partida)

            self.partidas_objetivo = partidas_validadas

            if not self.partidas_objetivo:
                reporte_lote["estado"] = (
                    "revision_manual"
                    if reporte_lote["partidas_revision_manual"] > 0
                    else "fallida"
                )

                logger.warning(
                    "No hay partidas aptas para procesar. "
                    "Revisión manual=%s. No se iniciará el navegador.",
                    reporte_lote["partidas_revision_manual"],
                )
                return
            resultado = await iniciar_sesion_sunarp()
        except Exception as exc:
            motivo = f"Error al iniciar sesión: {type(exc).__name__}: {exc}"

            self._registrar_incidencia(
                motivo,
                etapa="inicio_sesion",
            )
            self._finalizar_reporte("fallida", motivo)
            logger.exception("No se pudo iniciar sesión en SUNARP.")
            return

        if not isinstance(resultado, SunarpSession):
            motivo = "No se obtuvo una sesión autenticada de SUNARP."

            self._registrar_incidencia(
                motivo,
                etapa="inicio_sesion",
            )
            self._finalizar_reporte("fallida", motivo)
            logger.warning(motivo)
            return

        sesion = resultado
        hubo_fallo_cierre = False

        def registrar_partidas_pendientes(
            indice_actual: int,
            motivo: str,
        ) -> None:
            """Registra como no procesadas las partidas posteriores."""

            for pendiente in self.partidas_objetivo[indice_actual + 1:]:
                expediente_pendiente = expedientes_por_partida.get(
                    pendiente,
                    expediente_base,
                )
                oficina_pendiente = oficinas_por_partida.get(
                    pendiente,
                    self.oficina,
                )

                reporte_pendiente = self._crear_reporte_partida(
                    pendiente,
                    (
                        str(expediente_pendiente).strip()
                        if expediente_pendiente is not None
                        and str(expediente_pendiente).strip()
                        else None
                    ),
                    (
                        oficina_pendiente.strip()
                        if isinstance(oficina_pendiente, str)
                        else ""
                    ),
                )

                reporte_pendiente["estado"] = "no_procesada"
                reporte_pendiente["error"] = motivo

                self.reportes_partidas.append(reporte_pendiente)
                reporte_lote["partidas_fallidas"] += 1
                reporte_lote["partidas_revision_manual"] += int(
                    estado_partida == "revision_manual"
                )
                reporte_lote["partidas_no_procesadas"] += 1
                reporte_lote["partidas_no_procesadas"] += int(
                    estado_partida == "no_procesada"
                )

                logger.warning(
                    "Partida no procesada: partida=%s; motivo=%s",
                    pendiente,
                    motivo,
                )

        try:
            for indice, partida in enumerate(self.partidas_objetivo):
                logger.info(
                    "Procesando partida=%s; índice=%s/%s",
                    partida,
                    indice + 1,
                    len(self.partidas_objetivo),
                )

                oficina_valor = oficinas_por_partida.get(
                    partida,
                    self.oficina,
                )
                oficina = (
                    oficina_valor.strip()
                    if isinstance(oficina_valor, str)
                    else ""
                )

                expediente_valor = expedientes_por_partida.get(
                    partida,
                    expediente_base,
                )
                expediente = (
                    str(expediente_valor).strip()
                    if expediente_valor is not None
                    and str(expediente_valor).strip()
                    else None
                )

                reporte_partida = self._crear_reporte_partida(
                    partida,
                    expediente,
                    oficina,
                )

                clave_manifest = self.manifest.registrar_partida(
                    oficina=oficina,
                    area=AREA_REGISTRAL,
                    partida=partida,
                    expediente=expediente or "",
                )

                registro_manifest = self.manifest.obtener_partida(clave_manifest)

                if registro_manifest is None:
                    raise RuntimeError(
                        f"No se pudo recuperar del manifiesto la partida {partida}."
                    )

                estado_manifest = registro_manifest.get("estado")

                if estado_manifest in {"completada", "revision_manual"}:
                    reporte_partida["estado"] = estado_manifest
                    reporte_partida["error"] = registro_manifest.get("motivo")

                    self.reportes_partidas.append(reporte_partida)

                    if estado_manifest == "completada":
                        reporte_lote["partidas_completadas"] += 1
                    else:
                        # Revisión manual no es un fallo técnico.
                        reporte_lote.setdefault("partidas_revision_manual", 0)
                        reporte_lote["partidas_revision_manual"] += 1

                    logger.info(
                        "Partida omitida según el manifiesto: partida=%s; estado=%s",
                        partida,
                        estado_manifest,
                    )
                    continue

                if estado_manifest == "procesando":
                    pid_anterior = registro_manifest.get("pid_proceso")
                    ejecucion_anterior = registro_manifest.get("ejecucion_id")
                    proceso_activo = _proceso_sigue_activo(pid_anterior)

                    if proceso_activo is not False:
                        motivo = (
                            "No se recuperó automáticamente la partida porque no se "
                            "pudo demostrar que la ejecución anterior haya terminado. "
                            f"PID anterior={pid_anterior!r}; "
                            f"ejecución anterior={ejecucion_anterior!r}."
                        )

                        reporte_partida["estado"] = "no_procesada"
                        reporte_partida["error"] = motivo
                        reporte_lote["partidas_fallidas"] += 1
                        self.reportes_partidas.append(reporte_partida)

                        logger.warning(
                            "%s Partida=%s",
                            motivo,
                            partida,
                        )
                        continue

                    # El sistema confirmó que el PID anterior ya no existe.
                    # La partida puede pasar a error para permitir un nuevo intento.
                    self.manifest.actualizar_partida(
                        clave_manifest,
                        "error",
                        motivo=(
                            "Se detectó una ejecución anterior cuyo PID ya no existe. "
                            "Se permite un nuevo intento."
                        ),
                    )

                elif estado_manifest not in {"pendiente", "error"}:
                    motivo = (
                        f"Estado desconocido en el manifiesto: {estado_manifest!r}."
                    )

                    reporte_partida["estado"] = "no_procesada"
                    reporte_partida["error"] = motivo
                    reporte_lote["partidas_fallidas"] += 1
                    self.reportes_partidas.append(reporte_partida)

                    logger.error(
                        "No se procesará la partida %s: %s",
                        partida,
                        motivo,
                    )
                    continue

                self.execution_report = reporte_partida

                self.execution_report = reporte_partida
                sesion_perdida = False

                if not oficina:
                    motivo = (
                        f"No hay oficina registral válida para "
                        f"la partida {partida}."
                    )

                    self._registrar_incidencia(
                        motivo,
                        etapa="seleccion_oficina",
                    )
                    self._finalizar_reporte("fallida", motivo)
                    logger.error(motivo)

                else:
                    try:
                        async for documento in self._procesar_partida(
                            sesion,
                            partida,
                            expediente,
                            oficina,
                        ):
                            yield documento

                    except Exception as exc:
                        motivo = f"{type(exc).__name__}: {exc}"

                        self._registrar_incidencia(
                            motivo,
                            etapa="error_partida",
                        )

                        estado = (
                            "incompleta"
                            if reporte_partida["paginas_exitosas"] > 0
                            else "fallida"
                        )

                        self._finalizar_reporte(estado, motivo)

                        if _es_error_conexion_nodriver(exc):
                            sesion_perdida = True

                            logger.critical(
                                "Se perdió la conexión con Nodriver "
                                "durante la partida %s. Se detendrá "
                                "el lote para evitar reutilizar "
                                "una sesión inválida.",
                                partida,
                            )

                        logger.exception(
                            "Error procesando la partida %s.",
                            partida,
                        )

                if reporte_partida["estado"] == "en_ejecucion":
                    self._finalizar_reporte("fallida")

                # Guardar el reporte individual una sola vez.
                self.reportes_partidas.append(reporte_partida)

                # Acumular contadores en el reporte general.
                estado_partida = reporte_partida["estado"]

                reporte_lote["partidas_completadas"] += int(
                    estado_partida == "completada"
                )
                reporte_lote["partidas_incompletas"] += int(
                    estado_partida == "incompleta"
                )
                reporte_lote["partidas_fallidas"] += int(
                    estado_partida in {"fallida", "no_procesada"}
                )
                reporte_lote["partidas_sin_registros"] += int(
                    reporte_partida.get("resultado_busqueda")
                    == "sin_registros"
                )

                for campo in (
                    "paginas_previstas",
                    "paginas_intentadas",
                    "paginas_exitosas",
                    "paginas_fallidas",
                    "asientos_previstos",
                    "asientos_fallidos",
                    "documentos_emitidos",
                ):
                    reporte_lote[campo] += reporte_partida.get(
                        campo,
                        0,
                    )

                reporte_lote["incidencias"].extend(
                    reporte_partida.get("incidencias", [])
                )

                self.execution_report = reporte_lote

                # No volver a utilizar una sesión perdida.
                if sesion_perdida:
                    motivo = (
                        "Lote detenido porque se perdió la conexión "
                        "con el navegador SUNARP. Las partidas "
                        "restantes no se procesaron."
                    )

                    self._registrar_incidencia(
                        motivo,
                        etapa="sesion_perdida",
                    )
                    registrar_partidas_pendientes(indice, motivo)
                    break

                # Sin registros: continuar sin pulsar Regresar.
                if (
                    reporte_partida.get("resultado_busqueda")
                    == "sin_registros"
                ):
                    logger.info(
                        "Partida %s sin registros. Se continúa con "
                        "la siguiente sin ejecutar Regresar.",
                        partida,
                    )
                    continue

                # Si es la última partida, no hace falta regresar.
                if indice >= len(self.partidas_objetivo) - 1:
                    continue

                resultado_busqueda = reporte_partida.get(
                    "resultado_busqueda"
                )

                # Solo regresar cuando se recorrieron los asientos
                # y la partida terminó en estado completado/incompleto.
                if (
                    resultado_busqueda == "registros_encontrados"
                    and reporte_partida["estado"]
                    in {"completada", "incompleta"}
                ):
                    try:
                        regreso_ok = await regresar_a_busqueda(
                            sesion.page
                        )

                    except Exception as exc:
                        regreso_ok = False
                        motivo_regreso = (
                            f"Error al regresar a búsqueda después "
                            f"de la partida {partida}: "
                            f"{type(exc).__name__}: {exc}"
                        )

                        if _es_error_conexion_nodriver(exc):
                            sesion_perdida = True

                    else:
                        motivo_regreso = (
                            f"No se pudo confirmar el retorno a "
                            f"búsqueda después de la partida {partida}."
                        )

                    if regreso_ok:
                        logger.info(
                            "Retorno confirmado. Se continuará con "
                            "la siguiente partida después de %s.",
                            partida,
                        )

                    else:
                        self._registrar_incidencia(
                            motivo_regreso,
                            etapa="retorno_busqueda",
                        )

                        motivo_pendientes = (
                            "Lote detenido porque no se pudo confirmar "
                            "el retorno al formulario de búsqueda."
                        )

                        registrar_partidas_pendientes(
                            indice,
                            motivo_pendientes,
                        )

                        logger.error("%s", motivo_regreso)
                        break

                else:
                    # No iniciar otra búsqueda si el estado de la
                    # página no permite asegurar que está preparada.
                    motivo = (
                        f"No se puede continuar después de la partida "
                        f"{partida}: resultado_busqueda="
                        f"{resultado_busqueda!r}; estado="
                        f"{reporte_partida['estado']!r}. "
                        "No se confirmó un estado seguro del formulario."
                    )

                    self._registrar_incidencia(
                        motivo,
                        etapa="estado_busqueda_desconocido",
                    )

                    registrar_partidas_pendientes(indice, motivo)
                    logger.error(motivo)
                    break

            estados = [
                reporte.get("estado")
                for reporte in self.reportes_partidas
            ]

            if estados and all(
                estado == "completada"
                for estado in estados
            ):
                estado_lote = "completada"

            elif any(
                estado in {"completada", "incompleta"}
                for estado in estados
            ):
                estado_lote = "incompleta"

            else:
                estado_lote = "fallida"

            reporte_lote["estado"] = estado_lote

        except Exception as exc:
            motivo = f"{type(exc).__name__}: {exc}"

            reporte_lote["error"] = motivo
            reporte_lote["estado"] = (
                "incompleta"
                if reporte_lote["documentos_emitidos"] > 0
                else "fallida"
            )

            self.execution_report = reporte_lote
            self._registrar_incidencia(
                motivo,
                etapa="error_lote",
            )

            logger.exception(
                "Error general durante el lote SUNARP."
            )

        finally:
            self.execution_report = reporte_lote

            logger.info(
                "Cerrando el navegador de la sesión SUNARP."
            )

            try:
                await cerrar_navegador(sesion.browser)

            except Exception as exc:
                hubo_fallo_cierre = True

                motivo = (
                    "No se pudo cerrar limpiamente el navegador SUNARP: "
                    f"{type(exc).__name__}: {exc}"
                )

                self._registrar_incidencia(
                    motivo,
                    etapa="cierre_navegador",
                )

                logger.exception(motivo)

            if (
                hubo_fallo_cierre
                and reporte_lote["estado"] == "completada"
            ):
                reporte_lote["estado"] = "incompleta"

            logger.info(
                "Lote SUNARP finalizado: estado=%s; partidas=%s; "
                "completadas=%s; incompletas=%s; fallidas=%s; "
                "sin_registros=%s; documentos=%s; páginas fallidas=%s.",
                reporte_lote["estado"],
                len(self.reportes_partidas),
                reporte_lote["partidas_completadas"],
                reporte_lote["partidas_incompletas"],
                reporte_lote["partidas_fallidas"],
                reporte_lote["partidas_sin_registros"],
                reporte_lote["documentos_emitidos"],
                reporte_lote["paginas_fallidas"],
            )






