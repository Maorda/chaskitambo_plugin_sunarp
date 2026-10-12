import random
import nodriver as uc

async def seleccionar_area(page: uc.Tab, area: str):
    print(f"[Acción] Seleccionando área: {area}...")
    
    selectores = await page.select_all('nz-select-top-control.ant-select-selector')
    if len(selectores) < 2:
        print("[-] Error: No se encontró el segundo selector para el Área Registral.")
        return
        
    # 1. Clic en el desplegable de área
    await selectores[1].click()
    await page.sleep(random.uniform(0.5, 0.8))
    
    # 2. Localizamos su respectivo input de búsqueda activo
    inputs = await page.select_all('nz-select-search input.ant-select-selection-search-input')
    
    # CORRECCIÓN: Usamos inputs[-1] (el último) en lugar de inputs[1]. 
    # Ant Design a veces destruye del DOM el input anterior cuando se cierra. 
    # Usar [-1] te asegura tomar el input activo actual sin importar si hay 1 o 2 en el HTML.
    input_search = inputs[-1]
    
    await input_search.send_keys(area)
    await page.sleep(random.uniform(1.0, 1.5))
    
    # 3. Confirmamos haciendo clic en la opción
    opciones_desplegadas = await page.select_all('.ant-select-item-option')
    
    if opciones_desplegadas:
        await opciones_desplegadas[0].click()
    else:
        print(f"[-] Advertencia: No se renderizó la opción para {area}")
        
    await page.sleep(random.uniform(0.6, 1.0))