import asyncio
import json
from random import uniform
from typing import Awaitable, Callable
from urllib.parse import urlparse
import nodriver as uc

URL_SUNARP = "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/inicio"
URL_OBJETIVO = "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/servicio/busqueda"
TIEMPO_MAX_ESPERA_CAPTCHA = 540
INTERVALO_VERIFICACION = 1.0

async def tipear_lento(elemento, texto: str) -> None:
    await elemento.click()
    await asyncio.sleep(uniform(0.1, 0.3))
    for caracter in str(texto):
        await elemento.send_keys(caracter)
        await asyncio.sleep(uniform(0.05, 0.15))
    await asyncio.sleep(uniform(0.2, 0.5))

async def _valor_campo(page: uc.Tab, selector: str) -> str:
    selector_json = json.dumps(selector)
    resultado = await page.evaluate(f"(() => {{ const elemento = document.querySelector({selector_json}); return elemento ? String(elemento.value || '').trim() : null; }})()", await_promise=True)
    return "" if resultado is None else str(resultado).strip()

async def _boton_validar_habilitado(page: uc.Tab) -> bool:
    resultado = await page.evaluate("(() => { const boton = document.querySelector('button.btn-sunarp-green'); if (!boton) return false; const rect = boton.getBoundingClientRect(); const estilo = window.getComputedStyle(boton); return (!boton.disabled && boton.getAttribute('aria-disabled') !== 'true' && rect.width > 0 && rect.height > 0 && estilo.visibility !== 'hidden' && estilo.display !== 'none'); })()", await_promise=True)
    return resultado is True

async def _detectar_captcha_visual(page: uc.Tab) -> list[dict]:
    resultado = await page.evaluate("(() => { const palabras = /captcha|turnstile|challenge|recaptcha|hcaptcha|verify/i; return Array.from(document.querySelectorAll('iframe')).map((iframe) => { const rect = iframe.getBoundingClientRect(); const estilo = window.getComputedStyle(iframe); return { titulo: iframe.title || '', src: iframe.getAttribute('src') || '', nombre: iframe.name || '', visible: (rect.width > 0 && rect.height > 0 && estilo.display !== 'none' && estilo.visibility !== 'hidden'), ancho: Math.round(rect.width), alto: Math.round(rect.height) }; }).filter((item) => item.visible && palabras.test(item.titulo + ' ' + item.src + ' ' + item.nombre)); })()", await_promise=True)
    if isinstance(resultado, str):
        try:
            resultado = json.loads(resultado)
        except json.JSONDecodeError:
            return []
    return resultado if isinstance(resultado, list) else []

async def _esperar_verificacion_manual(page: uc.Tab) -> bool:
    inicio = asyncio.get_running_loop().time()
    desafio_anterior = None
    print("[CAPTCHA] Se detectó un posible desafío visual. Resuélvelo manualmente en el navegador.")
    while True:
        if asyncio.get_running_loop().time() - inicio >= TIEMPO_MAX_ESPERA_CAPTCHA:
            print("[CAPTCHA] Se agotó el tiempo de espera para la intervención.")
            return False
        try:
            desafios = await _detectar_captcha_visual(page)
            if not desafios:
                print("[CAPTCHA] El desafío visual ya no se detecta. Se volverá a comprobar el formulario.")
                return True
            resumen = json.dumps(desafios, ensure_ascii=False, sort_keys=True)
            if resumen != desafio_anterior:
                print("[CAPTCHA] Diagnóstico de los elementos visibles:\n" + resumen)
                desafio_anterior = resumen
        except Exception as exc:
            print(f"[CAPTCHA] No se pudo inspeccionar el desafío: {exc}. Se mantiene la espera manual.")
        await asyncio.sleep(INTERVALO_VERIFICACION)

async def _confirmar_sesion(page: uc.Tab, tiempo_maximo: int = 30) -> bool:
    inicio = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - inicio < tiempo_maximo:
        try:
            url_actual = str(page.url or "").strip()
            url_parseada = urlparse(url_actual)
            host_correcto = (url_parseada.hostname or "").lower() == "conoce-aqui.sunarp.gob.pe"
            ruta_actual = url_parseada.path.lower().rstrip("/")
            ruta_objetivo = urlparse(URL_OBJETIVO).path.lower().rstrip("/")
            if host_correcto and ruta_actual == ruta_objetivo:
                print(f"[OK] URL del servicio alcanzada: {url_actual}")
                return True
        except Exception:
            pass
        try:
            componente = await page.select("nz-select-top-control", timeout=1)
            if componente:
                print("[OK] Se detectó el selector del servicio de búsqueda.")
                return True
        except Exception:
            pass
        await asyncio.sleep(INTERVALO_VERIFICACION)
    return False

async def iniciar_sesion(page: uc.Tab, credencial: dict, antes_validar: Callable[[], Awaitable[None]] | None = None) -> bool:
    campos_requeridos = {
        "dni": credencial.get("dni"),
        "digito": credencial.get("digito"),
        "fecha_emision": credencial.get("fecha_emision"),
    }
    faltantes = [nombre for nombre, valor in campos_requeridos.items() if valor is None or not str(valor).strip()]
    if faltantes:
        print("[ERROR] Credencial incompleta. Faltan campos: " + ", ".join(faltantes))
        return False
    print(f"\n[Acción] Navegando a SUNARP: {URL_SUNARP}")
    try:
        await page.get(URL_SUNARP)
        await asyncio.sleep(uniform(3.0, 5.0))
        try:
            boton_aceptar = await page.select("button.accept-button", timeout=5)
            if boton_aceptar:
                await boton_aceptar.click()
                print("[OK] Se aceptó el modal inicial.")
                await asyncio.sleep(1.0)
        except Exception:
            print("[INFO] No se detectó el botón del modal inicial mediante su selector conocido.")
        selectores = (
            ("dni", 'input[formcontrolname="numeroDocumento"]'),
            ("digito", 'input[formcontrolname="digito"]'),
            ("fecha_emision", 'input[formcontrolname="fechaEmision"]'),
        )
        for nombre, selector in selectores:
            elemento = await page.select(selector, timeout=5)
            if not elemento:
                print(f"[ERROR] No se encontró el campo {nombre}.")
                return False
            await tipear_lento(elemento, str(campos_requeridos[nombre]))
            valor_actual = await _valor_campo(page, selector)
            if valor_actual != str(campos_requeridos[nombre]).strip():
                print(f"[ERROR] El campo {nombre} no coincide con el valor esperado después de escribirlo.")
                return False
        inicio_espera = asyncio.get_running_loop().time()
        boton_habilitado = False
        while asyncio.get_running_loop().time() - inicio_espera < 30:
            if await _boton_validar_habilitado(page):
                boton_habilitado = True
                break
            desafios = await _detectar_captcha_visual(page)
            if desafios:
                if not await _esperar_verificacion_manual(page):
                    return False
            await asyncio.sleep(INTERVALO_VERIFICACION)
        if not boton_habilitado:
            boton_habilitado = await _boton_validar_habilitado(page)
        if not boton_habilitado:
            print("[ERROR] El botón Validar no se habilitó. No se contabilizará ni se pulsará.")
            return False
        for nombre, selector in selectores:
            valor_actual = await _valor_campo(page, selector)
            if valor_actual != str(campos_requeridos[nombre]).strip():
                print(f"[ERROR] El campo {nombre} cambió antes de Validar.")
                return False
        if antes_validar is not None:
            await antes_validar()
        boton_validar = await page.select("button.btn-sunarp-green", timeout=5)
        if not boton_validar or not await _boton_validar_habilitado(page):
            print("[ERROR] El botón Validar dejó de estar habilitado antes del clic.")
            return False
        print("[Acción] Pulsando Validar.")
        await boton_validar.click()
        try:
            boton_continuar = await page.find("Sí, iniciar", timeout=3)
            if boton_continuar:
                print("[INFO] Se detectó una sesión previa.")
                await boton_continuar.click()
        except Exception:
            pass
        if await _confirmar_sesion(page, tiempo_maximo=30):
            print("[OK] Inicio de sesión confirmado.")
            return True
        desafios = await _detectar_captcha_visual(page)
        if desafios:
            if not await _esperar_verificacion_manual(page):
                return False
            if await _confirmar_sesion(page, tiempo_maximo=30):
                print("[OK] Inicio de sesión confirmado tras la intervención.")
                return True
        print("[ERROR] No se pudo confirmar el acceso al servicio después de pulsar Validar.")
        return False
    except Exception as exc:
        print(f"[ERROR] Excepción durante el inicio de sesión: {exc}")
        return False