import random
import nodriver as uc

async def seleccionar_oficina(page: uc.Tab, oficina: str):
    print(f"[Acción] Seleccionando oficina: {oficina}...")
    
    # 1. Hacemos clic en el contenedor del select para desplegarlo
    selectores = await page.select_all('nz-select-top-control.ant-select-selector')
    await selectores[0].click()
    await page.sleep(random.uniform(0.5, 0.8))
    
    # 2. Buscamos el input de búsqueda interno
    inputs = await page.select_all('nz-select-search input.ant-select-selection-search-input')
    input_search = inputs[0]
    
    await input_search.send_keys(oficina)
    
    # Aumentamos ligeramente la espera para que Angular filtre y renderice la lista
    await page.sleep(random.uniform(1.0, 1.5))
    
    # 3. CORRECCIÓN: Hacemos clic directo en la opción en lugar de usar "\n"
    opciones_desplegadas = await page.select_all('.ant-select-item-option')
    
    if opciones_desplegadas:
        await opciones_desplegadas[0].click()
    else:
        print(f"[-] Advertencia: No se renderizó la opción para {oficina}")
        
    # Pequeña pausa para asegurar que el menú se cerró
    await page.sleep(random.uniform(0.6, 1.0))
