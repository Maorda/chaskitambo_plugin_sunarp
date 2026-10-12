import random
import nodriver as uc

async def configurar_criterio_partida(page: uc.Tab, numero_partida: str):
    print("[Acción] Seleccionando opción 'Partida'...")
    # Buscamos la etiqueta label que envuelve el texto "Partida" de NG-ZORRO
    radio_label = await page.select('label.ant-radio-wrapper')
    await radio_label.click()
    await page.sleep(random.uniform(0.5, 0.8))
    
    print(f"[Acción] Escribiendo número de partida: {numero_partida}...")
    # Selector exacto basado en tu atributo formcontrolname="numero"
    input_numero = await page.select('input[formcontrolname="numero"]')
    
    # Limpiamos por seguridad antes de escribir
    await input_numero.click()
    await input_numero.send_keys(numero_partida)
    await page.sleep(random.uniform(0.5, 0.8))
