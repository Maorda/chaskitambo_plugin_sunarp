import random
import nodriver as uc

async def verificar_estado_y_previsualizar(page: uc.Tab) -> bool:
    print("[Acción] Validando respuestas del servidor...")
    await page.sleep(0.5)
    
    # 1. Comprobar si Sunarp devolvió 0 registros encontrados
    try:
        # Buscamos el texto indicador de cero resultados en el DOM
        cero_registros = await page.find("Total de registros encontrados: 0", timeout=2)
        if cero_registros:
            print("[-] [ALERTA SUNARP] La búsqueda arrojó 0 registros. La partida no existe en esta oficina o la oficina es incorrecta.")
            return False
    except Exception:
        pass

    # 2. Comprobar si saltó el modal con el error ORA de base de datos
    modal_error = await page.select('.modal-body, .alert-danger, snack-bar-container')
    if modal_error and "bad SQL grammar" in modal_error.text:
        print(f"\n[-] ERROR DETECTADO: El servidor devolvió un fallo SQL.")
        await page.save_screenshot("error_sql_sunarp.png")
        return False
        
    print("[Acción] ¡Registro encontrado! Procediendo a Previsualizar...")
    boton_previsualizar = await page.select('button.btn-preview', timeout=3)
    if not boton_previsualizar:
        print("[-] Error: No se encontró el botón de previsualización.")
        return False
        
    await boton_previsualizar.click()
    await page.sleep(3.0)
    return True
