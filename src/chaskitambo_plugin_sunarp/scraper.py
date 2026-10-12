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
    - Normaliza partidas (soporta cadenas separadas por espacios o listas).
    - Resolución nativa del expediente inyectado por el motor.
    - Resolución automática de oficinas con el módulo externo 'oficina_registral.py'.
    - Controla el tiempo máximo de sesión (9 minutos), rotación de DNI y reintentos.
    """
    def __init__(self, partida_objetivo: Any = None, **kwargs):
        self.partida_objetivo = self._normalizar_partidas(partida_objetivo)
        self.expediente = kwargs.get("expediente")
    
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
                # Maneja partidas múltiples separadas por espacios o comas (ej. "12331 123322")
                if " " in raw_partidas or "," in raw_partidas:
                    raw_partidas = re.split(r'[\s,]+', raw_partidas)
                else:
                    raw_partidas = re.findall(r'[\w\-]+', raw_partidas)
                    
        if isinstance(raw_partidas, list):
            return [str(p).strip().strip("'\"") for p in raw_partidas if p and str(p).strip() != "None"]
            
        return [str(raw_partidas).strip()]

    async def extract(self) -> AsyncGenerator[ChaskiDocument, None]:
        try:
            # 1. Comunicación nativa con el motor: lectura de ID principal, id_externo o atributos directos
            resolved_exp = getattr(self, "id", None) or getattr(self, "id_externo", None) or self.expediente

            # 2. Búsqueda profunda en diccionarios internos (metadatos, item, parent, etc.) si fuera necesario
            if not resolved_exp:
                for attr_name in ["metadatos", "item", "documento", "parent", "payload", "contexto", "detalle"]:
                    val = getattr(self, attr_name, None)
                    if isinstance(val, dict):
                        resolved_exp = val.get("expediente") or val.get("detalle", {}).get("expediente")
                        if resolved_exp:
                            break

            # 3. Búsqueda por expresión regular en todos los strings de self.__dict__
            if not resolved_exp:
                for k, v in self.__dict__.items():
                    if isinstance(v, str) and re.search(r'\d{5}-\d{4}-\d-\d{4}-', v):
                        resolved_exp = v
                        break
                    elif isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            if isinstance(sub_v, str) and re.search(r'\d{5}-\d{4}-\d-\d{4}-', sub_v):
                                resolved_exp = sub_v
                                break
                    if resolved_exp:
                        break

            self.expediente = resolved_exp
            logger.info(f" -> [SUNARP-PLUGIN] Partidas listas: {getattr(self, 'partida_objetivo', None)} | Expediente resuelto: {self.expediente}")

            if not getattr(self, "partida_objetivo", None):
                logger.error("[-] [SUNARP-PLUGIN] No se proporcionó ninguna 'partida_objetivo' válida.")
                return

            # 4. Si el expediente sigue siendo nulo, se registra en revisión manual con detalle
            if not self.expediente:
                logger.warning(f"[-] [REVISIÓN MANUAL] Expediente no detectado en el contexto del enriquecedor. Derivando a carpeta externa.")
                CARPETA_REVISION_MANUAL.mkdir(parents=True, exist_ok=True)
                
                registro_revision = {
                    "expediente": None,
                    "partidas_objetivo": getattr(self, "partida_objetivo", []),
                    "motivo": "Expediente nulo: no se encontró en atributos ni metadatos inyectados por Chaskitambo",
                    "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "atributos_disponibles": list(self.__dict__.keys())
                }
                
                nombre_archivo = f"expediente_nulo_{int(time.time())}.json"
                ruta_salida = CARPETA_REVISION_MANUAL / nombre_archivo
                ruta_salida.write_text(json.dumps(registro_revision, indent=4, ensure_ascii=False), encoding="utf-8")
                
                logger.info(f"[OK] Registro de depuración guardado en {ruta_salida}.")
                return

            # 5. Resolución de la oficina registral mediante el módulo externo
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
                
                exp_str = str(self.expediente).replace('/', '_').replace('-', '_')
                ruta_salida = CARPETA_REVISION_MANUAL / f"expediente_{exp_str}.json"
                ruta_salida.write_text(json.dumps(registro_revision, indent=4, ensure_ascii=False), encoding="utf-8")
                return

            area = "PROPIEDAD INMUEBLE PREDIAL"
            partidas_pendientes = list(self.partida_objetivo)

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