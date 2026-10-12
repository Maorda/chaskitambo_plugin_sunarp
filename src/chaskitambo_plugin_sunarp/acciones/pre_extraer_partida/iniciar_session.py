# Archivo: acciones/iniciar_sesion.py
import asyncio
from random import uniform
import nodriver as uc

URL_SUNARP = "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/inicio"
URL_OBJETIVO = "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/servicio/busqueda"

async def tipear_lento(elemento, texto: str):
    """Escribe texto caracter por caracter con pausas aleatorias para evadir Cloudflare."""
    await elemento.click()
    await asyncio.sleep(uniform(0.1, 0.3))
    for char in str(texto):
        await elemento.send_keys(char)
        await asyncio.sleep(uniform(0.05, 0.15))
    await asyncio.sleep(uniform(0.2, 0.5))

async def iniciar_sesion(page: uc.Tab, credencial: dict) -> bool:
    print(f"\n[Acción] Navegando a SUNARP: {URL_SUNARP}")
    await page.get(URL_SUNARP)
    
    print("[Acción] Esperando carga inicial de la página...")
    # Damos un poco más de tiempo por si la conexión es lenta
    await asyncio.sleep(uniform(5.0, 7.0))
    
    # 1. Manejar el Modal "Sí Acepto" (Mejorado)
    try:
        print("[Acción] Buscando botón 'Sí Acepto' en el modal...")
        # Esperamos hasta 10 segundos para que aparezca el modal
        btn_aceptar = await page.select('button.accept-button', timeout=10)
        
        if btn_aceptar:
            await btn_aceptar.click()
            print("[OK] Botón 'Sí Acepto' presionado (por clase css).")
            await asyncio.sleep(uniform(2.0, 3.0))
        else:
            # Fallback: buscar por el texto si cambiaron la clase
            btn_txt = await page.find("Acepto", timeout=3)
            if btn_txt:
                await btn_txt.click()
                print("[OK] Botón 'Sí Acepto' presionado (por texto).")
                await asyncio.sleep(uniform(2.0, 3.0))
            else:
                print("[-] No se encontró el botón del modal inicial. Quizás ya no estaba visible.")
    except Exception as e:
        print(f"[-] Nota sobre el modal inicial: {e}")

    # 2. Llenar el formulario de DNI
    print(f"[Acción] Iniciando sesión con DNI: {credencial['dni']}")
    
    try:
        input_dni = await page.select('input[formcontrolname="numeroDocumento"]', timeout=5)
        if not input_dni:
            print("[-] Error: No se encontró el input de DNI.")
            return False
        await tipear_lento(input_dni, credencial['dni'])
        
        input_digito = await page.select('input[formcontrolname="digito"]', timeout=5)
        if not input_digito:
            print("[-] Error: No se encontró el input de dígito de verificación.")
            return False
        await tipear_lento(input_digito, str(credencial.get('digito', '')))
        
        input_fecha = await page.select('input[formcontrolname="fechaEmision"]', timeout=5)
        if not input_fecha:
            print("[-] Error: No se encontró el input de fecha de emisión.")
            return False
        await tipear_lento(input_fecha, credencial.get('fecha_emision', ''))
        
        # 3. Espera pasiva para Cloudflare Turnstile
        print("[Acción] Esperando 7 segundos para la validación de Cloudflare Turnstile...")
        await asyncio.sleep(7.0)
        
        # 4. Presionar Validar
        btn_validar = await page.select('button.btn-sunarp-green', timeout=5)
        if not btn_validar:
            print("[-] Error: No se encontró el botón Validar.")
            return False
        print("[Acción] Haciendo clic en el botón 'Validar'...")
        await btn_validar.click()
        
        # 5. CONTROL DE SESIÓN ABIERTA
        print("[Acción] Verificando si existe una sesión previa abierta...")
        await asyncio.sleep(3.0) 
        try:
            # Usamos find para buscar la palabra Continuar
            btn_continuar = await page.find("Sí, iniciar", timeout=3)
            if btn_continuar:
                print("[¡ALERTA!] Se detectó una sesión previa abierta. Haciendo clic en 'Continuar'...")
                await btn_continuar.click()
                await asyncio.sleep(2.0)
        except Exception:
            print("[OK] No hay alertas de sesión previa o no se detectó el botón.")

        # 6. Monitoreo estricto del cambio de URL y componentes internos
        print("[Acción] Monitoreando el cambio de URL hacia la zona de búsqueda...")
        
        tiempo_espera_max = 20  
        intervalo = 1.0        
        pasos = int(tiempo_espera_max / intervalo)
        
        for _ in range(pasos):
            await asyncio.sleep(intervalo)
            
            # Verificación 1: Por URL
            try:
                url_actual = page.url.strip()
                if URL_OBJETIVO in url_actual.lower() or "servicio/busqueda" in url_actual.lower():
                    print(f"[OK] ¡Redirección de URL exitosa! URL alcanzada: {url_actual}")
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                pass
            
            # Verificación 2: Por DOM (¡AQUÍ ESTABA EL BUCLE INFINITO!)
            try:
                # 💡 SOLUCIÓN: Agregamos timeout=1 para que si no lo encuentra, siga el bucle en lugar de congelarse
                componente_interno = await page.select('nz-select-top-control', timeout=1)
                if componente_interno:
                    print(f"[OK] Componente DOM interno detectado (URL actual: {page.url})")
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                pass

        print(f"[-] Tiempo de espera agotado ({tiempo_espera_max}s). La URL no cambió a la zona de búsqueda.")
        return False
            
    except Exception as ex:
        print(f"[-] Excepción durante el proceso de login: {ex}")
        return False