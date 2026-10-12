# Archivo: orquestador_sunarp.py
import asyncio
import os
import json
import time
import re
from datetime import datetime
import nodriver as uc

from .acciones.extraer_partida import extraer_partida
from .acciones.pre_extraer_partida.iniciar_session import iniciar_sesion

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_CREDENCIALES = os.path.join(BASE_DIR, "..", "buscardniperu", "credenciales.json")
ARCHIVO_PARTIDAS = os.path.join(BASE_DIR, "partidas.json")
ARCHIVO_OFICINAS = os.path.join(BASE_DIR, "oficina_registral.json") # 💡 Ruta al archivo JSON

LIMITE_INGRESOS = 5
TIEMPO_MAX_SESION = 9 * 60  # 9 minutos por sesión
MAX_INTENTOS_PARTIDA = 3    # Límite de reintentos para partidas con error

def extraer_codigo_oficina(expediente: str) -> str:
    """Extrae el código de oficina (ej: '2501') del número de expediente."""
    if not expediente:
        return "N/A"
    patron = r'^\d{5}-\d{4}-\d-(\d{4})-'
    match = re.match(patron, expediente.strip())
    if match:
        return match.group(1)
    partes = expediente.split('-')
    if len(partes) >= 4:
        return partes[3]
    return "N/A"

def cargar_oficinas():
    """Carga la lista de oficinas registrales desde oficina_registral.json."""
    ruta_limpia = os.path.normpath(ARCHIVO_OFICINAS)
    try:
        with open(ruta_limpia, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[!] ADVERTENCIA: No se encontró el archivo '{ARCHIVO_OFICINAS}'. Se usarán códigos numéricos.")
        return []

def obtener_nombre_oficina(codigo: str, lista_oficinas: list) -> str:
    """Traduce el código numérico de oficina (ej: '2501') a su nombre en texto ('CHIMBOTE')."""
    for oficina in lista_oficinas:
        if oficina.get("codigo") == codigo:
            return oficina.get("nombre", codigo)
    return codigo  # Si no lo encuentra, devuelve el código numérico original

def cargar_partidas():
    ruta_limpia = os.path.normpath(ARCHIVO_PARTIDAS)
    try:
        with open(ruta_limpia, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def guardar_partidas(partidas):
    ruta_limpia = os.path.normpath(ARCHIVO_PARTIDAS)
    with open(ruta_limpia, 'w', encoding='utf-8') as f:
        json.dump(partidas, f, indent=2, ensure_ascii=False)

def cargar_credenciales():
    ruta_limpia = os.path.normpath(ARCHIVO_CREDENCIALES)
    try:
        with open(ruta_limpia, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def guardar_credenciales(credenciales):
    ruta_limpia = os.path.normpath(ARCHIVO_CREDENCIALES)
    with open(ruta_limpia, 'w', encoding='utf-8') as f:
        json.dump(credenciales, f, indent=2, ensure_ascii=False)

def verificar_y_limpiar_bloqueos(credenciales):
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    hubo_cambios = False
    
    for cred in credenciales:
        fecha_cred = cred.get("fecha_ciclo", "")
        if fecha_cred and fecha_cred < hoy_str:
            print(f"[INFO] Nuevo día detectado ({hoy_str}). Reseteando DNI {cred['dni']}.")
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
    for cred in credenciales:
        if cred.get("ingresos_ciclo", 0) < LIMITE_INGRESOS:
            return cred
    return None

async def main():
    # Cargar oficinas desde JSON externo
    oficinas_registrales = cargar_oficinas()
    
    credenciales = cargar_credenciales()
    if not credenciales:
        print("[-] No se encontraron credenciales en el archivo JSON.")
        return
        
    credenciales = verificar_y_limpiar_bloqueos(credenciales)
        
    partidas = cargar_partidas()
    if not partidas:
        print("[-] No se encontraron partidas en el archivo JSON.")
        return

    abortar_ejecucion = False

    while not abortar_ejecucion:
        pendientes = [
            p for p in partidas 
            if (p.get("estado") == "pendiente" or (p.get("estado") == "error" and p.get("intentos", 0) < MAX_INTENTOS_PARTIDA))
        ]
        
        if not pendientes:
            print("\n[FIN] No hay más partidas pendientes o reintentables disponibles.")
            break

        credencial_actual = obtener_dni_disponible(credenciales)
        if not credencial_actual:
            print("\n[FIN] Todos los DNIs han agotado sus 5 intentos por hoy.")
            break
            
        print(f"\n[SESIÓN INICIADA] Usando DNI: {credencial_actual['dni']} (Intento {credencial_actual['ingresos_ciclo'] + 1} de {LIMITE_INGRESOS})")
        
        browser = None
        try:
            hoy_str = datetime.now().strftime("%Y-%m-%d")
            if credencial_actual["ingresos_ciclo"] == 0:
                credencial_actual["fecha_ciclo"] = hoy_str
                
            credencial_actual["ingresos_ciclo"] += 1
            guardar_credenciales(credenciales)
            
            browser = await uc.start(browser_args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.main_tab
            
            exito_login = await iniciar_sesion(page, credencial_actual)
            
            if not exito_login:
                print("\n[ESTADO CRÍTICO] El login falló.")
                print("[PROTECCIÓN] Abortando ejecución global para evitar quemar más DNIs.")
                abortar_ejecucion = True
                break
            
            print("[INFO] Autenticación completada. Inicia cronómetro de 9 min.")
            inicio_sesion = time.time()
            
            while not abortar_ejecucion:
                pendientes_actuales = [
                    p for p in partidas 
                    if (p.get("estado") == "pendiente" or (p.get("estado") == "error" and p.get("intentos", 0) < MAX_INTENTOS_PARTIDA))
                ]
                if not pendientes_actuales:
                    break

                tiempo_transcurrido = time.time() - inicio_sesion
                if tiempo_transcurrido > TIEMPO_MAX_SESION:
                    print("[RELOJ] 9 minutos alcanzados. Cerrando sesión para rotar DNI.")
                    break
                
                item = pendientes_actuales[0]
                
                # Extracción y traducción usando la lista cargada del archivo JSON
                codigo_oficina = extraer_codigo_oficina(item.get("expediente", ""))
                nombre_oficina = obtener_nombre_oficina(codigo_oficina, oficinas_registrales)
                
                item["intentos"] = item.get("intentos", 0) + 1
                guardar_partidas(partidas)
                
                try:
                    print(f"\n[PROCESANDO] Partida: {item['partida']} | Oficina: {nombre_oficina} (Código: {codigo_oficina}) [Intento #{item['intentos']}]")
                    
                    exito = await extraer_partida(page, nombre_oficina, item['area'], item['partida'])
                    
                    if exito:
                        item["estado"] = "procesado"
                        item["error"] = None
                        print(f"[OK] Partida {item['partida']} extraída correctamente.")
                    else:
                        item["estado"] = "error" 
                        item["error"] = "Fallo en verificación/modal de SUNARP"
                        print(f"[-] Partida {item['partida']} marcó error. Podrá ser reintentada.")
                        
                    item["fecha_procesamiento"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    guardar_partidas(partidas)
                    
                except Exception as ex_item:
                    print(f"[-] Excepción menor al procesar la partida {item['partida']}: {ex_item}")
                    item["estado"] = "error"
                    item["error"] = str(ex_item)
                    guardar_partidas(partidas)
                
        except Exception as e:
            print(f"\n[ERROR CRÍTICO DEL SISTEMA] Ocurrió una excepción: {e}")
            abortar_ejecucion = True
            break
            
        finally:
            if browser:
                print("[SESIÓN] Cerrando navegador...")
                try:
                    await browser.stop()
                except Exception:
                    pass
                await asyncio.sleep(2)

    if abortar_ejecucion:
        print("\n[ALERTA] Se detuvo la ejecución.")

if __name__ == '__main__':
    uc.loop().run_until_complete(main())