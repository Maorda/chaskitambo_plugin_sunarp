import ast
import re
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, AsyncGenerator
from loguru import logger
import nodriver as uc

from chaskitambo import BaseScraper, ChaskiDocument

# Importamos las acciones internas del plugin
from .acciones.pre_extraer_partida.iniciar_session import iniciar_sesion
from .acciones.extraer_partida import extraer_partida

# Importamos la resolución de oficinas desde tu archivo externo
from .oficina_registral import obtener_nombre_oficina

# Rutas internas robustas basadas en pathlib
BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_CREDENCIALES = BASE_DIR / "credenciales.json"
CARPETA_REVISION_MANUAL = Path("./revisar_manualmente")

LIMITE_INGRESOS = 5
MAX_INTENTOS_PARTIDA = 3
TIEMPO_MAX_SESION = 9 * 60  # 9 minutos límite por sesión antes de rotar DNI


def cargar_credenciales():
    """Carga el archivo de credenciales DNI protegido dentro del paquete."""
    try:
        with open(ARCHIVO_CREDENCIALES, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"[-] No se encontró el archivo de credenciales en: {ARCHIVO_CREDENCIALES}")
        return None


def guardar_credenciales(credenciales):
    """Actualiza y persiste los contadores de ingresos de los DNI."""
    with open(ARCHIVO_CREDENCIALES, 'w', encoding='utf-8') as f:
        json.dump(credenciales, f, indent=2, ensure_ascii=False)


def verificar_y_limpiar_bloqueos(credenciales):
    """Resetea los contadores diarios si el ciclo corresponde a un nuevo día."""
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    hubo_cambios = False
    
    for cred in credenciales:
        fecha_cred = cred.get("fecha_ciclo", "")
        if fecha_cred and fecha_cred < hoy_str:
            logger.info(f"[INFO] Nuevo día detectado ({hoy_str}). Reseteando contadores del DNI {cred['dni']}.")
            cred["ingresos_ciclo"] = 0
            cred["fecha_ciclo"] = hoy_str
            hubo_cambios = True
        elif not fecha_cred and cred.get("ingresos_ciclo", 0) > 0:
            cred["fecha_ciclo"] = hoy_str
            hubo_cambios = True
            
    if hubo_cambios:
        guardar_credenciales(credenciales)
        
    return credenciales


def obtener_dni_disponible(credenciales):
    """Retorna el primer DNI que no haya agotado sus 5 ingresos permitidos."""
    for cred in credenciales:
        if cred.get("ingresos_ciclo", 0) < LIMITE_INGRESOS:
            return cred
    return None


class SunarpScraper(BaseScraper):
    """
    Scraper oficial de SUNARP integrado con Chaskitambo.
    - Normaliza partidas (incluso si vienen como string de lista `['123']`).
    - Resuelve automáticamente la oficina registral mediante el módulo externo.
    - Controla el tiempo máximo de sesión (9 minutos), rotación de DNI y manejo de reintentos.
    """
    def __init__(self, partida_objetivo: Any = None, expediente: Any = None, **kwargs):
        logger.info(f" [DEBUG-SUNARP] --- INSPECCIONANDO CONTEXTO DE INSTANCIA ---")
        logger.info(f" [DEBUG-SUNARP] Atributos disponibles en self: {list(self.__dict__.keys())}")
        logger.info(f" [DEBUG-SUNARP] kwargs recibidos del YAML: {list(kwargs.keys())}")
        
        resolved_expediente = None
        
        # 1. Buscar en los atributos que Chaskitambo inyecta en self
        for attr_name, attr_val in self.__dict__.items():
            if isinstance(attr_val, str) and re.search(r'\d{5}-\d{4}', attr_val):
                resolved_expediente = attr_val
                logger.info(f" [DEBUG-SUNARP] Hallado en atributo self.{attr_name}: {resolved_expediente}")
                break
            elif isinstance(attr_val, dict):
                if "expediente" in attr_val and isinstance(attr_val["expediente"], str):
                    resolved_expediente = attr_val["expediente"]
                    logger.info(f" [DEBUG-SUNARP] Hallado en dict self.{attr_name}['expediente']: {resolved_expediente}")
                    break
                if "detalle" in attr_val and isinstance(attr_val["detalle"], dict):
                    if "expediente" in attr_val["detalle"]:
                        resolved_expediente = attr_val["detalle"]["expediente"]
                        logger.info(f" [DEBUG-SUNARP] Hallado en self.{attr_name}['detalle']['expediente']: {resolved_expediente}")
                        break

        # 2. Si aún no se encuentra, revisar si viene en kwargs
        if not resolved_expediente:
            for k, v in kwargs.items():
                if isinstance(v, str) and re.search(r'\d{5}-\d{4}', v):
                    resolved_expediente = v
                    logger.info(f" [DEBUG-SUNARP] Hallado in kwargs['{k}']: {resolved_expediente}")
                    break

        self.expediente = resolved_expediente
        logger.info(f" [DEBUG-SUNARP] Expediente final asignado a self.expediente: {self.expediente!r}")
        logger.info(f" [DEBUG-SUNARP] -----------------------------------------------")
        
        self.partida_objetivo = self._normalizar_partidas(partida_objetivo)

    def _normalizar_partidas(self, raw_partidas) -> List[str]:
        if not raw_partidas:
            return []
            
        if isinstance(raw_partidas, str):
            raw_partidas = raw_partidas.strip()
            if raw_partidas.startswith("[") and raw_partidas.endswith("]"):
                try:
                    parsed = ast.literal_eval(raw_partidas)
                    if isinstance(parsed, list):
                        raw_partidas = parsed
                except Exception:
                    raw_partidas = re.findall(r'[\w\-]+', raw_partidas)
            else:
                raw_partidas = re.findall(r'[\w\-]+', raw_partidas)
                
        if isinstance(raw_partidas, list):
            return [str(p).strip().strip("'\"") for p in raw_partidas if p]
            
        return [str(raw_partidas).strip()]

    async def extract(self) -> AsyncGenerator[ChaskiDocument, None]:
        try:
            # 1. Resolución segura del expediente
            resolved_exp = getattr(self, "id", None) or getattr(self, "id_externo", None)
            
            if not resolved_exp:
                for attr in ["expediente", "documento_principal", "item", "metadatos", "contexto", "payload"]:
                    val = getattr(self, attr, None)
                    if isinstance(val, str) and len(val) > 5:
                        resolved_exp = val
                        break
                    elif isinstance(val, dict):
                        resolved_exp = val.get("expediente") or (val.get("detalle", {}) or {}).get("expediente")
                        if resolved_exp:
                            break

            if resolved_exp:
                self.expediente = resolved_exp

            logger.info(f" -> [SUNARP-PLUGIN v1] Partidas listas: {getattr(self, 'partida_objetivo', None)} | Expediente: {self.expediente}")

            if not getattr(self, "partida_objetivo", None):
                logger.error("[-] [SUNARP-PLUGIN] No se proporcionó ninguna 'partida_objetivo' válida.")
                return

            # 2. Resolución de oficina con el módulo externo y control de revisión manual
            nombre_oficina = obtener_nombre_oficina(self.expediente)
            
            if not nombre_oficina:
                logger.warning(f"[-] [REVISIÓN MANUAL] Expediente con código no mapeado: {self.expediente}. Derivando a carpeta externa.")
                
                CARPETA_REVISION_MANUAL.mkdir(parents=True, exist_ok=True)
                
                registro_revision = {
                    "expediente": self.expediente,
                    "partidas_objetivo": getattr(self, "partida_objetivo", []),
                    "motivo": "Código de corte judicial ausente en oficina_registral.py",
                    "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                exp_str = str(self.expediente or "desconocido").replace('/', '_').replace('-', '_')
                ruta_salida = CARPETA_REVISION_MANUAL / f"expediente_{exp_str}.json"
                ruta_salida.write_text(json.dumps(registro_revision, indent=4, ensure_ascii=False), encoding="utf-8")
                
                logger.info(f"[OK] Expediente guardado en {ruta_salida}. Omitiendo scraping de Sunarp.")
                return

            area = "PROPIEDAD INMUEBLE PREDIAL"
            
            # Normalizar partida_objetivo por si llega como string plano o lista
            raw_partidas = self.partida_objetivo
            if isinstance(raw_partidas, str):
                try:
                    import ast
                    raw_partidas = ast.literal_eval(raw_partidas)
                except Exception:
                    raw_partidas = [raw_partidas]
            
            partidas_pendientes = list(raw_partidas)

        except Exception as e:
            logger.error(f"[-] ERROR CRÍTICO EN INICIALIZACIÓN DE EXTRACT: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return

        # Bucle principal de sesiones y rotación de DNI
        while partidas_pendientes:
            credenciales = cargar_credenciales()
            if not credenciales:
                logger.error("[-] No se pudieron cargar las credenciales de Sunarp.")
                break

            credenciales = verificar_y_limpiar_bloqueos(credenciales)
            credencial_actual = obtener_dni_disponible(credenciales)
            
            if not credencial_actual:
                logger.error("[-] Todos los DNIs han agotado sus 5 intentos diarios permitidos.")
                break

            logger.info(f"[SESIÓN INICIADA] Usando DNI: {credencial_actual['dni']} (Ingreso #{credencial_actual.get('ingresos_ciclo', 0) + 1}/{LIMITE_INGRESOS}) | Oficina: {nombre_oficina}")

            browser = None
            try:
                hoy_str = datetime.now().strftime("%Y-%m-%d")
                if credencial_actual.get("ingresos_ciclo", 0) == 0:
                    credencial_actual["fecha_ciclo"] = hoy_str
                    
                credencial_actual["ingresos_ciclo"] += 1
                guardar_credenciales(credenciales)

                # Iniciar navegador con nodriver
                browser = await uc.start(browser_args=["--no-sandbox", "--disable-setuid-sandbox"])
                page = browser.main_tab

                # Iniciar sesión manejando Cloudflare y el modal "Sí, iniciar"
                exito_login = await iniciar_sesion(page, credencial_actual)
                if not exito_login:
                    logger.error("[-] [ESTADO CRÍTICO] El login en Sunarp falló con el DNI actual.")
                    break

                logger.info("[INFO] Autenticación completada. Inicia cronómetro de sesión de 9 min.")
                inicio_sesion = time.time()

                # Procesar partidas dentro de la ventana de los 9 minutos
                while partidas_pendientes:
                    tiempo_transcurrido = time.time() - inicio_sesion
                    if tiempo_transcurrido > TIEMPO_MAX_SESION:
                        logger.warning("[RELOJ] 9 minutos alcanzados. Cerrando sesión actual para rotar DNI y continuar con las partidas restantes...")
                        break

                    partida = partidas_pendientes.pop(0)
                    if not partida:
                        continue

                    logger.info(f"[PROCESANDO] Consultando Partida: {partida} en Oficina: {nombre_oficina}...")
                    
                    exito = await extraer_partida(page, nombre_oficina, area, str(partida))

                    if exito:
                        logger.info(f"[OK] Partida {partida} extraída correctamente.")
                        
                        ruta_pdf = Path(f"./descargas_sunarp/partida_{partida}.pdf")
                        pdf_bytes = ruta_pdf.read_bytes() if ruta_pdf.is_file() else b""

                        if not pdf_bytes:
                            logger.warning(f"[-] Advertencia: La partida {partida} se procesó, pero no se encontró el PDF en disco.")

                        yield ChaskiDocument(
                            id_externo=str(partida),
                            fuente="sunarp",
                            metadatos={
                                "partida_registral": str(partida),
                                "oficina_registral": nombre_oficina,
                                "expediente": self.expediente
                            },
                            pdf_bytes=pdf_bytes,
                            nombre_archivo_sugerido=f"sunarp_{partida}.pdf"
                        )
                    else:
                        logger.warning(f"[-] La extracción de la partida {partida} marcó error o la tabla quedó vacía.")

            except Exception as e:
                logger.error(f"[-] Excepción crítica en el ciclo del scraper de Sunarp: {e}")
            finally:
                if browser:
                    logger.info("[SESIÓN] Cerrando navegador...")
                    try:
                        await browser.stop()
                    except Exception:
                        pass
                    await asyncio.sleep(2)