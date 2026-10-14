import ast
import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, List
import nodriver as uc
from loguru import logger
from chaskitambo import BaseScraper, ChaskiDocument
from .acciones.pre_extraer_partida.iniciar_session import iniciar_sesion
from .acciones.extraer_partida import extraer_partida
from .extraer_bytes_pdf import extraer_bytes_pdf
from .manifest import SunarpManifest
from .oficina_registral import obtener_nombre_oficina

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_CONFIG_LOCAL = BASE_DIR / "sunarp_local.json"
ARCHIVO_CREDENCIALES = BASE_DIR / "credenciales.json"
CARPETA_REVISION_MANUAL = BASE_DIR / "revisar_manualmente"
CARPETA_DESCARGAS = BASE_DIR / "descargas_sunarp"
LIMITE_INGRESOS = 5
MAX_INTENTOS_PARTIDA = 3
TIEMPO_MAX_SESION = 9 * 60
TOLERANCIA_EJECUCION_ABANDONADA = TIEMPO_MAX_SESION + 60
AREA_REGISTRAL = "PROPIEDAD INMUEBLE PREDIAL"
PATRON_EXPEDIENTE = re.compile(r"\d{5}-\d{4}-\d-\d{4}-[A-Z]{2}-[A-Z]{2}-\d{2}", re.IGNORECASE)

def cargar_credenciales() -> list[dict] | None:
    try:
        with ARCHIVO_CREDENCIALES.open("r", encoding="utf-8") as archivo:
            credenciales = json.load(archivo)
        if not isinstance(credenciales, list):
            logger.error("El archivo de credenciales debe contener una lista JSON.")
            return None
        validas = []
        for indice, credencial in enumerate(credenciales):
            if not isinstance(credencial, dict):
                logger.warning("Se omite la credencial de índice {}: no es un objeto.", indice)
                continue
            dni = str(credencial.get("dni", "")).strip()
            if not dni:
                logger.warning("Se omite la credencial de índice {}: no contiene DNI.", indice)
                continue
            try:
                ingresos = int(credencial.get("ingresos_ciclo", 0))
            except (TypeError, ValueError):
                logger.warning("Contador de ingresos inválido para el DNI {}.", dni)
                continue
            if ingresos < 0:
                logger.warning("Contador negativo para el DNI {}.", dni)
                continue
            credencial["dni"] = dni
            credencial["ingresos_ciclo"] = ingresos
            validas.append(credencial)
        return validas
    except FileNotFoundError:
        logger.error("No se encontró el archivo de credenciales: {}", ARCHIVO_CREDENCIALES)
        return None
    except (OSError, json.JSONDecodeError):
        logger.exception("No se pudieron cargar las credenciales SUNARP.")
        return None

def guardar_credenciales(credenciales: list[dict]) -> None:
    ARCHIVO_CREDENCIALES.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with ARCHIVO_CREDENCIALES.open("r", encoding="utf-8") as archivo:
            original = json.load(archivo)
        if not isinstance(original, list):
            raise ValueError("El archivo de credenciales dejó de contener una lista JSON.")
        with __import__("tempfile").NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=ARCHIVO_CREDENCIALES.parent,
            prefix=f"{ARCHIVO_CREDENCIALES.name}.",
            suffix=".tmp", delete=False,
        ) as archivo:
            temporal = Path(archivo.name)
            json.dump(credenciales, archivo, indent=2, ensure_ascii=False)
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ARCHIVO_CREDENCIALES)
    except Exception:
        if temporal is not None:
            try:
                temporal.unlink(missing_ok=True)
            except OSError:
                pass
        raise

def verificar_y_limpiar_bloqueos(credenciales: list[dict]) -> list[dict]:
    hoy_str = datetime.now().astimezone().date().isoformat()
    hubo_cambios = False
    for credencial in credenciales:
        fecha_ciclo = str(credencial.get("fecha_ciclo", "")).strip()
        if fecha_ciclo and fecha_ciclo < hoy_str:
            logger.info("Nuevo día. Se restablece el contador del DNI {}.", credencial["dni"])
            credencial["ingresos_ciclo"] = 0
            credencial["fecha_ciclo"] = hoy_str
            hubo_cambios = True
        elif not fecha_ciclo:
            credencial["fecha_ciclo"] = hoy_str
            hubo_cambios = True
    if hubo_cambios:
        guardar_credenciales(credenciales)
    return credenciales

def obtener_dni_disponible(credenciales: list[dict]) -> dict | None:
    for credencial in credenciales:
        if credencial.get("ingresos_ciclo", 0) < LIMITE_INGRESOS:
            return credencial
    return None

def proceso_activo(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if getattr(exc, "winerror", None) == 87:
            return False
        if getattr(exc, "errno", None) == 3:
            return False
        logger.warning("No se pudo determinar con certeza si el PID {} está activo: {}", pid, exc)
        return True

def antiguedad_heartbeat_segundos(registro: dict) -> float | None:
    heartbeat = registro.get("heartbeat_en")
    if not heartbeat:
        heartbeat = registro.get("inicio_procesamiento_en")
    if not heartbeat:
        return None
    try:
        fecha = datetime.fromisoformat(str(heartbeat))
        if fecha.tzinfo is None:
            fecha = fecha.astimezone()
        return max(0.0, (datetime.now().astimezone() - fecha).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None

def recuperar_partida_abandonada(manifest: SunarpManifest, clave: str, registro: dict, partida: str) -> bool:
    pid = registro.get("pid_proceso")
    if pid is not None and proceso_activo(pid):
        logger.warning("Partida {} omitida: el manifiesto indica que sigue asignada al PID activo {}. No se iniciará otra ejecución.", partida, pid)
        return False
    antiguedad = antiguedad_heartbeat_segundos(registro)
    if pid is None and (antiguedad is None or antiguedad < TOLERANCIA_EJECUCION_ABANDONADA):
        logger.warning("Partida {} en estado procesando, sin PID verificable y sin evidencia suficiente de abandono. Se conserva el bloqueo.", partida)
        return False
    motivo = "Ejecución anterior abandonada: el proceso propietario ya no existe." if pid is not None else "Ejecución anterior abandonada: el heartbeat superó el umbral de recuperación y no existe un PID registrado."
    manifest.actualizar_partida(clave=clave, estado="error", motivo=motivo)
    logger.warning("Partida {} recuperada de una ejecución abandonada. Los asientos descargados se conservarán.", partida)
    return True

def validar_pdf(pdf_bytes: bytes) -> None:
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise RuntimeError("No se obtuvieron bytes PDF válidos.")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("Los bytes no contienen una firma PDF válida.")
    if b"%%EOF" not in pdf_bytes[-2048:]:
        raise RuntimeError("No se encontró el marcador final %%EOF.")

def archivo_pdf_valido(ruta: Path) -> bool:
    try:
        if not ruta.is_file() or ruta.stat().st_size == 0:
            return False
        with ruta.open("rb") as archivo:
            firma = archivo.read(5)
            archivo.seek(max(0, ruta.stat().st_size - 2048))
            final = archivo.read()
        return firma == b"%PDF-" and b"%%EOF" in final
    except OSError:
        return False

def nombre_archivo_seguro(valor: str) -> str:
    nombre = re.sub(r"[^\w.-]+", "_", str(valor), flags=re.UNICODE)
    return nombre.strip("._") or "sin_identificador"

def guardar_revision_manual(nombre_archivo: str, datos: dict) -> Path:
    CARPETA_REVISION_MANUAL.mkdir(parents=True, exist_ok=True)
    nombre_seguro = nombre_archivo_seguro(nombre_archivo)
    ruta = CARPETA_REVISION_MANUAL / nombre_seguro
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Registro de revisión manual guardado en {}", ruta)
    return ruta

def normalizar_partidas(raw_partidas: Any) -> List[str]:
    if raw_partidas is None:
        return []
    if isinstance(raw_partidas, str):
        raw_partidas = raw_partidas.strip()
        if not raw_partidas:
            return []
        if raw_partidas.startswith("[") and raw_partidas.endswith("]"):
            try:
                parsed = ast.literal_eval(raw_partidas)
                raw_partidas = parsed if isinstance(parsed, list) else [raw_partidas]
            except (ValueError, SyntaxError):
                raw_partidas = re.findall(r"[\w-]+", raw_partidas)
        elif " " in raw_partidas or "," in raw_partidas:
            raw_partidas = re.split(r"[\s,]+", raw_partidas)
        else:
            raw_partidas = [raw_partidas]
    elif not isinstance(raw_partidas, (list, tuple, set)):
        raw_partidas = [raw_partidas]
    resultado = []
    vistos = set()
    for partida in raw_partidas:
        if partida is None:
            continue
        texto = str(partida).strip().strip("'\"")
        if not texto or texto == "None":
            continue
        if texto not in vistos:
            vistos.add(texto)
            resultado.append(texto)
    return resultado

async def validar_partida_en_interfaz(page: uc.Tab, partida_esperada: str) -> None:
    js_script = r"""
    (() => {
        const normalizar = (texto) => (texto || "").replace(/\s+/g, " ").trim();
        const contenedores = Array.from(document.querySelectorAll(".side-bar-container"));
        const contenedor = contenedores.find(elemento => elemento.querySelector(".cabecera-container") && elemento.querySelector(".columna-lista .row-container"));
        if (!contenedor) {
            return JSON.stringify({ ok: false, motivo: "No se encontró la cabecera de la partida." });
        }
        const cabecera = normalizar(contenedor.querySelector(".cabecera-container").innerText);
        const coincidencia = cabecera.match(/Partida\s*:\s*([^\s]+)/i);
        return JSON.stringify({ ok: Boolean(coincidencia), partida: coincidencia ? coincidencia[1] : null, cabecera: cabecera });
    })()
    """
    resultado = await page.evaluate(js_script, await_promise=True)
    if isinstance(resultado, dict):
        datos = resultado
    elif isinstance(resultado, str):
        try:
            datos = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError("La validación de partida devolvió un JSON inválido.") from exc
    else:
        raise RuntimeError("La validación de partida no devolvió una respuesta válida.")
    partida_encontrada = str(datos.get("partida") or "").strip()
    esperada = str(partida_esperada).strip()
    if not datos.get("ok") or not partida_encontrada:
        raise RuntimeError("No se pudo validar la partida en la cabecera de SUNARP.")
    if partida_encontrada != esperada:
        raise RuntimeError(f"Partida incorrecta en la previsualización: solicitada={esperada!r}, encontrada={partida_encontrada!r}.")
    logger.info("Partida validada en SUNARP: {}.", partida_encontrada)

async def seleccionar_asiento_en_barra_lateral(page: uc.Tab, identificador_asiento: str) -> dict:
    identificador_asiento = str(identificador_asiento).strip()
    identificador_js = json.dumps(identificador_asiento, ensure_ascii=False)
    js_script = f"""
    (() => {{
        const objetivo = {identificador_js};
        const normalizar = (texto) => (texto || "").replace(/\\s+/g, " ").trim();
        const contenedores = Array.from(document.querySelectorAll(".side-bar-container"));
        const contenedor = contenedores.find(elemento => elemento.querySelector(".columna-lista .row-container"));
        if (!contenedor) {{
            return JSON.stringify({{ ok: false, motivo: "No se encontró la barra lateral de asientos." }});
        }}
        const filas = Array.from(contenedor.querySelectorAll(".columna-lista .row-container"));
        for (const fila of filas) {{
            const texto = normalizar(fila.children[0]?.innerText);
            const coincidencia = texto.match(/N\\s*[°ºo.]?\\s*Asiento\\s*:\\s*([^\\s]+)/i);
            if (!coincidencia) continue;
            const numero = normalizar(coincidencia[1]);
            if (numero !== objetivo) continue;
            const botonesPagina = Array.from(fila.querySelectorAll(".pagina .boton-pagina"));
            if (!botonesPagina.length) {{
                return JSON.stringify({{ ok: false, motivo: "El asiento no tiene botones de página.", asiento: numero }});
            }}
            const paginaSeleccionada = normalizar(botonesPagina[0].innerText);
            botonesPagina[0].click();
            return JSON.stringify({{ ok: true, asiento: numero, pagina_seleccionada: paginaSeleccionada, cantidad_botones_pagina: botonesPagina.length, descripcion: texto }});
        }}
        return JSON.stringify({{ ok: false, motivo: "No se encontró el asiento solicitado.", asiento_buscado: objetivo }});
    }})()
    """
    try:
        resultado = await page.evaluate(js_script, await_promise=True)
    except Exception as exc:
        raise RuntimeError(f"No se pudo seleccionar el asiento {identificador_asiento}: {exc}") from exc
    if isinstance(resultado, dict):
        datos = resultado
    elif isinstance(resultado, str):
        try:
            datos = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError("La selección del asiento devolvió JSON inválido.") from exc
    else:
        raise RuntimeError("La selección del asiento no devolvió el JSON esperado.")
    if not isinstance(datos, dict) or not datos.get("ok"):
        motivo = datos.get("motivo", "No se pudo seleccionar el asiento.") if isinstance(datos, dict) else "Respuesta inválida al seleccionar el asiento."
        raise RuntimeError(motivo)
    logger.info("Asiento {} seleccionado. Página inicial: {}. Botones de página: {}.", datos["asiento"], datos["pagina_seleccionada"], datos["cantidad_botones_pagina"])
    await page.sleep(1.5)
    return datos

class SunarpScraper(BaseScraper):
    def __init__(self, partida_objetivo: Any = None, modo_local: bool = False, **kwargs):
        self.partida_objetivo = normalizar_partidas(partida_objetivo)
        self.expediente = kwargs.get("expediente")
        self.modo_local = modo_local
        self.manifest = SunarpManifest()

    @staticmethod
    def _buscar_expediente_recursivo(valor: Any) -> str | None:
        if isinstance(valor, str):
            coincidencia = PATRON_EXPEDIENTE.search(valor)
            return coincidencia.group(0) if coincidencia else None
        if isinstance(valor, dict):
            for clave in ("expediente", "numero_expediente", "id_expediente"):
                candidato = valor.get(clave)
                if isinstance(candidato, str):
                    coincidencia = PATRON_EXPEDIENTE.search(candidato)
                    if coincidencia:
                        return coincidencia.group(0)
            for subvalor in valor.values():
                encontrado = SunarpScraper._buscar_expediente_recursivo(subvalor)
                if encontrado:
                    return encontrado
        elif isinstance(valor, (list, tuple)):
            for subvalor in valor:
                encontrado = SunarpScraper._buscar_expediente_recursivo(subvalor)
                if encontrado:
                    return encontrado
        return None

    def _resolver_expediente(self) -> str | None:
        for atributo in ("id", "id_externo", "expediente"):
            valor = getattr(self, atributo, None)
            if valor:
                encontrado = self._buscar_expediente_recursivo(valor)
                if encontrado:
                    return encontrado
        for atributo in ("metadatos", "item", "documento", "parent", "payload", "contexto", "detalle"):
            valor = getattr(self, atributo, None)
            encontrado = self._buscar_expediente_recursivo(valor)
            if encontrado:
                return encontrado
        return self._buscar_expediente_recursivo(self.__dict__)

    def _crear_documento(self, partida: str, asiento_info: dict, nombre_oficina: str, pdf_bytes: bytes, ruta_pdf: Path, pagina_seleccionada: str | None = None) -> ChaskiDocument:
        identificador = str(asiento_info["asiento"]).strip()
        partida_archivo = nombre_archivo_seguro(partida)
        asiento_archivo = nombre_archivo_seguro(identificador)
        descripcion = f"Acto: {asiento_info.get('acto', '')}; Año: {asiento_info.get('anio') or ''}; Rubro: {asiento_info.get('rubro', '')}; Páginas: {', '.join(str(p) for p in asiento_info.get('paginas', []))}"
        return ChaskiDocument(
            id_externo=f"{partida}:{identificador}",
            fuente="sunarp",
            metadatos={
                "partida_registral": str(partida),
                "asiento_registral": identificador,
                "descripcion_asiento": descripcion,
                "acto": asiento_info.get("acto", ""),
                "anio": asiento_info.get("anio"),
                "rubro": asiento_info.get("rubro", ""),
                "paginas_registrales": asiento_info.get("paginas", []),
                "oficina_registral": nombre_oficina,
                "expediente": self.expediente,
                "bytes_pdf": len(pdf_bytes),
                "ruta_pdf_local": str(ruta_pdf.resolve()),
                "pdf_independiente_por_asiento": True,
                "pagina_seleccionada": pagina_seleccionada,
            },
            pdf_bytes=pdf_bytes,
            nombre_archivo_sugerido=f"sunarp_{partida_archivo}_asiento_{asiento_archivo}.pdf",
        )

    async def _procesar_asiento(self, page: uc.Tab, partida: str, asiento_info: dict, clave_manifest: str, ejecucion_id: str, nombre_oficina: str) -> AsyncGenerator[ChaskiDocument, None]:
        identificador = str(asiento_info["asiento"]).strip()
        registro_partida = self.manifest.obtener_partida(clave_manifest)
        if registro_partida is None:
            raise RuntimeError(f"No existe la partida {partida} en el manifiesto.")
        registro_asiento = registro_partida.get("asientos", {}).get(identificador)
        if registro_asiento and registro_asiento.get("estado") == "descargado":
            ruta_registrada = registro_asiento.get("archivo")
            if ruta_registrada:
                ruta_existente = Path(ruta_registrada)
                if archivo_pdf_valido(ruta_existente):
                    pdf_bytes = ruta_existente.read_bytes()
                    validar_pdf(pdf_bytes)
                    logger.info("Partida {} | Asiento {}: se reutiliza el PDF local y se vuelve a entregar al motor.", partida, identificador)
                    yield self._crear_documento(
                        partida=partida,
                        asiento_info=asiento_info,
                        nombre_oficina=nombre_oficina,
                        pdf_bytes=pdf_bytes,
                        ruta_pdf=ruta_existente,
                    )
                    return
            logger.warning("El manifiesto indica que el asiento {} está descargado, pero el archivo no existe o no es válido. Se descargará nuevamente.", identificador)
            self.manifest.actualizar_asiento(
                clave_partida=clave_manifest,
                asiento=identificador,
                estado="pendiente",
                archivo=None,
                tamano_bytes=None,
                motivo="El archivo previamente registrado no existe o no supera la validación básica del PDF.",
            )
        self.manifest.registrar_heartbeat_partida(clave=clave_manifest, ejecucion_id=ejecucion_id)
        descripcion_asiento = f"Acto: {asiento_info.get('acto', '')}; Año: {asiento_info.get('anio') or ''}; Rubro: {asiento_info.get('rubro', '')}; Páginas: {', '.join(str(p) for p in asiento_info.get('paginas', []))}"
        self.manifest.registrar_asiento(clave_partida=clave_manifest, asiento=identificador, descripcion=descripcion_asiento)
        self.manifest.actualizar_asiento(clave_partida=clave_manifest, asiento=identificador, estado="descargando", motivo=None)
        try:
            seleccion = await seleccionar_asiento_en_barra_lateral(page, identificador)
            self.manifest.registrar_heartbeat_partida(clave=clave_manifest, ejecucion_id=ejecucion_id)
            logger.info("Extrayendo PDF.js: partida {} | asiento {}.", partida, identificador)
            pdf_bytes = await extraer_bytes_pdf(page)
            validar_pdf(pdf_bytes)
            CARPETA_DESCARGAS.mkdir(parents=True, exist_ok=True)
            partida_archivo = nombre_archivo_seguro(partida)
            asiento_archivo = nombre_archivo_seguro(identificador)
            ruta_pdf = CARPETA_DESCARGAS / f"sunarp_{partida_archivo}_asiento_{asiento_archivo}.pdf"
            ruta_temporal = ruta_pdf.with_name(f"{ruta_pdf.name}.{uuid.uuid4().hex}.tmp")
            try:
                with ruta_temporal.open("wb") as archivo:
                    archivo.write(pdf_bytes)
                    archivo.flush()
                    os.fsync(archivo.fileno())
                os.replace(ruta_temporal, ruta_pdf)
            finally:
                ruta_temporal.unlink(missing_ok=True)
            if not archivo_pdf_valido(ruta_pdf):
                raise RuntimeError("El PDF guardado no supera la validación posterior a la escritura.")
            tamano_guardado = ruta_pdf.stat().st_size
            if tamano_guardado != len(pdf_bytes):
                raise RuntimeError(f"El tamaño guardado no coincide con los bytes extraídos: memoria={len(pdf_bytes)}, disco={tamano_guardado}.")
            self.manifest.actualizar_asiento(
                clave_partida=clave_manifest,
                asiento=identificador,
                estado="descargado",
                archivo=str(ruta_pdf.resolve()),
                tamano_bytes=tamano_guardado,
                motivo=None,
            )
            logger.info("PDF validado: partida {} | asiento {} | {} bytes | {}", partida, identificador, tamano_guardado, ruta_pdf)
            yield self._crear_documento(
                partida=partida,
                asiento_info=asiento_info,
                nombre_oficina=nombre_oficina,
                pdf_bytes=pdf_bytes,
                ruta_pdf=ruta_pdf,
                pagina_seleccionada=seleccion.get("pagina_seleccionada"),
            )
        except Exception as exc:
            try:
                self.manifest.actualizar_asiento(clave_partida=clave_manifest, asiento=identificador, estado="error", motivo=str(exc))
            except Exception:
                logger.exception("No se pudo registrar el error del asiento {}.", identificador)
            raise

    async def _extraer_modo_local(self) -> AsyncGenerator[ChaskiDocument, None]:
        if not ARCHIVO_CONFIG_LOCAL.is_file():
            logger.error("No existe el archivo de configuración local: {}", ARCHIVO_CONFIG_LOCAL)
            return
        try:
            with ARCHIVO_CONFIG_LOCAL.open("r", encoding="utf-8") as archivo:
                configuracion = json.load(archivo)
        except (OSError, json.JSONDecodeError):
            logger.exception("No se pudo leer la configuración local de SUNARP.")
            return
        expedientes = configuracion.get("expedientes") if isinstance(configuracion, dict) else None
        if not isinstance(expedientes, list) or not expedientes:
            logger.error("El JSON local no contiene una lista válida y no vacía en 'expedientes'.")
            return
        for registro in expedientes:
            if not isinstance(registro, dict):
                logger.warning("Se omite un registro local que no es un objeto JSON.")
                continue
            expediente = registro.get("expediente")
            partidas = registro.get("partidas_registrales")
            if not isinstance(expediente, str) or not expediente.strip() or not isinstance(partidas, list) or not partidas:
                logger.warning("Registro local incompleto; se omite: {}", registro)
                continue
            logger.info("Prueba local: expediente={} | partidas={}", expediente.strip(), len(partidas))
            scraper = SunarpScraper(partida_objetivo=partidas, expediente=expediente.strip(), modo_local=False)
            async for documento in scraper.extract():
                yield documento

    async def extract(self) -> AsyncGenerator[ChaskiDocument, None]:
        if self.modo_local:
            async for documento in self._extraer_modo_local():
                yield documento
            return
        try:
            expediente = self._resolver_expediente()
            self.expediente = str(expediente).strip() if expediente else None
            logger.info("Partidas objetivo: {} | Expediente resuelto: {}", self.partida_objetivo, self.expediente)
            if not self.partida_objetivo:
                logger.error("No se proporcionó ninguna partida registral válida.")
                return
            if not self.expediente:
                guardar_revision_manual(
                    f"expediente_nulo_{int(time.time())}.json",
                    {
                        "expediente": None,
                        "partidas_objetivo": self.partida_objetivo,
                        "motivo": "No se encontró el expediente en los atributos ni en los metadatos del contexto.",
                        "fecha_registro": datetime.now().astimezone().isoformat(),
                        "atributos_disponibles": list(self.__dict__.keys()),
                    },
                )
                return
            nombre_oficina = obtener_nombre_oficina(self.expediente)
            if not nombre_oficina:
                guardar_revision_manual(
                    f"expediente_{nombre_archivo_seguro(self.expediente)}.json",
                    {
                        "expediente": self.expediente,
                        "partidas_objetivo": self.partida_objetivo,
                        "motivo": "No se encontró el código de corte judicial en oficina_registral.py.",
                        "fecha_registro": datetime.now().astimezone().isoformat(),
                    },
                )
                return
            partidas_pendientes = []
            claves_manifest = {}
            for partida in self.partida_objetivo:
                clave = self.manifest.registrar_partida(oficina=nombre_oficina, area=AREA_REGISTRAL, partida=partida, expediente=self.expediente)
                registro = self.manifest.obtener_partida(clave)
                estado = registro.get("estado") if registro else None
                if estado == "completada":
                    logger.info("Partida {} omitida: ya figura como completada.", partida)
                    continue
                if estado == "revision_manual":
                    logger.warning("Partida {} omitida: requiere revisión manual.", partida)
                    continue
                if estado == "procesando":
                    if not recuperar_partida_abandonada(manifest=self.manifest, clave=clave, registro=registro, partida=partida):
                        continue
                claves_manifest[partida] = clave
                partidas_pendientes.append(partida)
            if not partidas_pendientes:
                logger.info("No hay partidas disponibles para procesar.")
                return
        except Exception:
            logger.exception("Error crítico durante la inicialización del scraper SUNARP.")
            return

        intentos_partida = {partida: 0 for partida in partidas_pendientes}
        dnis_intentados = set()

        while partidas_pendientes:
            credenciales = cargar_credenciales()
            if not credenciales:
                logger.error("No se pudieron cargar las credenciales SUNARP.")
                break
            credenciales = verificar_y_limpiar_bloqueos(credenciales)
            credencial_actual = next(
                (
                    credencial
                    for credencial in credenciales
                    if credencial.get("ingresos_ciclo", 0) < LIMITE_INGRESOS
                    and credencial["dni"] not in dnis_intentados
                ),
                None,
            )
            if not credencial_actual:
                logger.error("Todos los DNI agotaron sus {} ingresos diarios. Las partidas restantes permanecen en el manifiesto.", LIMITE_INGRESOS)
                break
            dni_actual = credencial_actual["dni"]
            logger.info("Sesión SUNARP | DNI: {} | Ingreso {}/{} | Oficina: {}", dni_actual, credencial_actual.get("ingresos_ciclo", 0) + 1, LIMITE_INGRESOS, nombre_oficina)
            browser = None
            inicio_sesion = None
            try:
                browser = await uc.start(
                    browser_args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                page = browser.main_tab


                async def registrar_ingreso():
                    """
                    Persiste el ingreso inmediatamente antes del clic en Validar.
                    """
                    hoy_str = datetime.now().astimezone().date().isoformat()

                    credencial_actual["fecha_ciclo"] = hoy_str
                    credencial_actual["ingresos_ciclo"] = (
                        int(credencial_actual.get("ingresos_ciclo", 0)) + 1
                    )

                    guardar_credenciales(credenciales)

                    logger.info(
                        "Ingreso contabilizado para DNI {}: {}/{}.",
                        dni_actual,
                        credencial_actual["ingresos_ciclo"],
                        LIMITE_INGRESOS,
                    )


                exito_login = await iniciar_sesion(
                    page,
                    credencial_actual,
                    antes_validar=registrar_ingreso,
                )

                if not exito_login:
                    dnis_intentados.add(dni_actual)

                    logger.error(
                        "No se confirmó el inicio de sesión con el DNI {}. "
                        "Se intentará con otra credencial disponible.",
                        dni_actual,
                    )
                    continue

                # El DNI no necesita excluirse después de un inicio exitoso.
                dnis_intentados.clear()

                inicio_sesion = time.monotonic()

                logger.info(
                    "Autenticación completada. Límite de sesión: {} segundos.",
                    TIEMPO_MAX_SESION,
                )

                while partidas_pendientes:
                    tiempo_transcurrido = time.monotonic() - inicio_sesion
                    if tiempo_transcurrido >= TIEMPO_MAX_SESION:
                        logger.warning("Se alcanzó el límite de sesión. Se continuará con otra sesión.")
                        break
                    partida = partidas_pendientes[0]
                    clave_manifest = claves_manifest[partida]
                    ejecucion_id = uuid.uuid4().hex
                    asiento_actual = None
                    try:
                        self.manifest.iniciar_ejecucion_partida(clave=clave_manifest, ejecucion_id=ejecucion_id, pid_proceso=os.getpid())
                        self.manifest.registrar_heartbeat_partida(clave=clave_manifest, ejecucion_id=ejecucion_id)
                        logger.info("Procesando partida {} en oficina {}.", partida, nombre_oficina)
                        asientos = await extraer_partida(page, nombre_oficina, AREA_REGISTRAL, partida)
                        if not asientos:
                            raise RuntimeError("No se obtuvo una lista válida de asientos desde la barra lateral de SUNARP.")
                        await validar_partida_en_interfaz(page, partida)
                        identificadores = set()
                        for asiento_info in asientos:
                            if not isinstance(asiento_info, dict):
                                raise RuntimeError("La lista contiene un asiento con formato inválido.")
                            identificador = str(asiento_info.get("asiento", "")).strip()
                            if not identificador:
                                raise RuntimeError("Se encontró un asiento sin identificador.")
                            if identificador in identificadores:
                                raise RuntimeError(f"Identificador de asiento duplicado: {identificador}")
                            identificadores.add(identificador)
                        logger.info("Partida {}: {} asientos identificados.", partida, len(asientos))
                        for asiento_info in asientos:
                            identificador = str(asiento_info["asiento"]).strip()
                            descripcion = f"Acto: {asiento_info.get('acto', '')}; Año: {asiento_info.get('anio') or ''}; Rubro: {asiento_info.get('rubro', '')}; Páginas: {', '.join(str(p) for p in asiento_info.get('paginas', []))}"
                            self.manifest.registrar_asiento(clave_partida=clave_manifest, asiento=identificador, descripcion=descripcion)
                        rotar_sesion = False
                        for asiento_info in asientos:
                            tiempo_transcurrido = time.monotonic() - inicio_sesion
                            if tiempo_transcurrido >= TIEMPO_MAX_SESION:
                                self.manifest.actualizar_partida(
                                    clave=clave_manifest,
                                    estado="error",
                                    motivo="Se alcanzó el límite de sesión durante el procesamiento. Los asientos descargados se conservan y los restantes podrán reintentarse.",
                                )
                                rotar_sesion = True
                                break
                            asiento_actual = str(asiento_info["asiento"]).strip()
                            async for documento in self._procesar_asiento(
                                page=page,
                                partida=partida,
                                asiento_info=asiento_info,
                                clave_manifest=clave_manifest,
                                ejecucion_id=ejecucion_id,
                                nombre_oficina=nombre_oficina,
                            ):
                                yield documento
                            asiento_actual = None
                        if rotar_sesion:
                            break
                        registro_final = self.manifest.obtener_partida(clave_manifest)
                        asientos_finales = registro_final.get("asientos", {}) if registro_final else {}
                        faltantes = []
                        for asiento_info in asientos:
                            identificador = str(asiento_info["asiento"]).strip()
                            registro_asiento = asientos_finales.get(identificador)
                            if not registro_asiento or registro_asiento.get("estado") != "descargado":
                                faltantes.append(identificador)
                        if faltantes:
                            raise RuntimeError(f"No se completaron todos los asientos. Pendientes o fallidos: {faltantes}")
                        self.manifest.actualizar_partida(clave=clave_manifest, estado="completada", motivo=None)
                        logger.info("Partida {} completada: {} asientos descargados.", partida, len(asientos))
                        partidas_pendientes.pop(0)
                        intentos_partida.pop(partida, None)
                    except Exception as exc:
                        logger.exception("Error procesando la partida {}.", partida)
                        if asiento_actual is not None:
                            try:
                                registro_actual = self.manifest.obtener_partida(clave_manifest)
                                asientos_actuales = registro_actual.get("asientos", {}) if registro_actual else {}
                                asiento_registrado = asientos_actuales.get(asiento_actual)
                                if asiento_registrado and asiento_registrado.get("estado") != "descargado":
                                    self.manifest.actualizar_asiento(clave_partida=clave_manifest, asiento=asiento_actual, estado="error", motivo=str(exc))
                            except Exception:
                                logger.exception("No se pudo actualizar el estado del asiento {}.", asiento_actual)
                        try:
                            registro_actual = self.manifest.obtener_partida(clave_manifest)
                            if registro_actual and registro_actual.get("estado") == "procesando":
                                self.manifest.actualizar_partida(clave=clave_manifest, estado="error", motivo=str(exc))
                        except Exception:
                            logger.exception("No se pudo actualizar el manifiesto de la partida {}.", partida)
                        intentos_partida[partida] = intentos_partida.get(partida, 0) + 1
                        intentos = intentos_partida[partida]
                        if intentos < MAX_INTENTOS_PARTIDA:
                            logger.warning("Partida {}: intento fallido {}/{}. Se reintentará en otra sesión.", partida, intentos, MAX_INTENTOS_PARTIDA)
                            break
                        logger.error("Partida {} agotó los {} intentos. Se conserva como error en el manifiesto y se continuará con la siguiente partida.", partida, MAX_INTENTOS_PARTIDA)
                        partidas_pendientes.pop(0)
                        intentos_partida.pop(partida, None)
                        break
            except Exception:
                logger.exception("Error durante la sesión SUNARP.")
                if partidas_pendientes:
                    partida = partidas_pendientes[0]
                    clave = claves_manifest[partida]
                    try:
                        registro = self.manifest.obtener_partida(clave)
                        if registro and registro.get("estado") == "procesando":
                            self.manifest.actualizar_partida(clave=clave, estado="error", motivo="La sesión SUNARP terminó con una excepción no controlada.")
                    except Exception:
                        logger.exception("No se pudo actualizar el estado de la partida tras el error de sesión.")
            finally:
                if browser is not None:
                    logger.info("Cerrando navegador SUNARP.")
                    try:
                        browser.stop()
                    except Exception:
                        logger.exception("No se pudo cerrar el navegador correctamente.")
                    await asyncio.sleep(2)
        logger.info("Finalizó el ciclo SUNARP. Partidas aún pendientes: {}.", len(partidas_pendientes))