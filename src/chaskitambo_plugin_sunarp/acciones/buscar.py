import asyncio
import nodriver as uc

async def ejecutar_busqueda_y_esperar_spinner(page: uc.Tab):
    print("[Acción] Presionando botón 'Buscar'...")
    
    # Usamos la clase exacta que vimos en tu HTML
    try:
        boton_buscar = await page.select('button.btn-buscar-partida')
        
        if not boton_buscar:
            print("[-] Error: No se encontró el botón con la clase 'btn-buscar-partida'.")
            return
            
        await boton_buscar.click()
        
    except Exception as e:
        print(f"[-] Error al intentar hacer clic en buscar: {e}")
        return
    
    print("[Acción] Monitoreando spinner de Angular...")
    await page.sleep(1.0) # Tiempo para que Angular renderice el loader
    
    for _ in range(30): # Máximo 15 segundos de espera
        try:
            # Buscamos el spinner de NG-ZORRO con un timeout bajísimo
            spinner = await page.select('.ant-spin, .spinner, .loading-overlay', timeout=0.1)
            if not spinner:
                break
        except Exception:
            # Si falla el timeout, el spinner ya desapareció
            break
        await asyncio.sleep(0.5)
        
    print("[OK] Spinner desaparecido. Búsqueda completada.")