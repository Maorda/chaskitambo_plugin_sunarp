from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from dataclasses import dataclass
import nodriver as uc
from dotenv import dotenv_values

logger = logging.getLogger("sunarp.login")
MENSAJE_BLOQUEO = "Lo sentimos, ha alcanzado la cantidad máxima permitida de 5 ingresos al sistema. Mañana podrá volver a utilizar nuestro servicio gratuito Conoce Aquí."
NOMBRE_DIRECTORIO_CONFIG = Path("Chaskitambo") / "plugins" / "sunarp"

@dataclass(slots=True)
class SunarpSession:
    browser: Any
    page: uc.Tab

def obtener_directorio_configuracion() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "Chaskitambo" / "plugins" / "sunarp"

def obtener_ruta_env() -> Path:
    """Obtiene el .env ubicado en la raíz del proyecto del plugin."""
    ruta = Path(__file__).resolve()

    for directorio in ruta.parents:
        if (
            directorio.name == "chaskitambo_plugin_sunarp"
            and (directorio / "pyproject.toml").is_file()
        ):
            ruta_env = directorio / ".env"

            if not ruta_env.is_file():
                raise FileNotFoundError(
                    f"No existe el archivo de configuración del plugin: {ruta_env}"
                )

            return ruta_env

    raise FileNotFoundError(
        "No se encontró la raíz del proyecto chaskitambo_plugin_sunarp "
        "a partir de la ubicación de iniciar_session.py."
    )


async def _detectar_modal_verificacion_requerida(page) -> bool:
    """
    Detecta exclusivamente el SweetAlert2 que solicita resolver el captcha.
    No interactúa con el captcha de Cloudflare.
    """
    expresion = r"""
    (() => {
        const modal = document.querySelector(
            '.swal2-popup.swal2-show'
        );

        if (!modal) {
            return false;
        }

        const titulo = (
            modal.querySelector('.swal2-title')?.innerText || ''
        ).trim();

        const contenido = (
            modal.querySelector('.swal2-html-container')?.innerText ||
            modal.querySelector('.swal2-content')?.innerText ||
            ''
        ).trim();

        return (
            titulo === 'Verificación requerida' &&
            contenido.includes(
                'Por favor resuelve el captcha antes de continuar'
            )
        );
    })()
    """

    try:
        resultado = await evaluar(page, expresion)
        return bool(resultado)
    except Exception as exc:
        logger.debug(
            "No se pudo comprobar el modal de verificación: {}",
            exc,
        )
        return False


async def _cerrar_modal_verificacion_requerida(page) -> bool:
    """
    Espera brevemente el modal de verificación y pulsa su botón OK.
    Devuelve True si encontró y cerró el aviso.
    """
    tiempo_maximo = 5.0
    intervalo = 0.25
    transcurrido = 0.0

    while transcurrido < tiempo_maximo:
        if await _detectar_modal_verificacion_requerida(page):
            boton = await page.select("button.swal2-confirm")

            if boton is None:
                logger.warning(
                    "El aviso de verificación apareció, "
                    "pero no se encontró el botón OK."
                )
                return False

            await boton.click()
            await asyncio.sleep(0.3)

            logger.info(
                "Aviso 'Verificación requerida' cerrado. "
                "Se requiere resolver el captcha manualmente."
            )
            return True

        await asyncio.sleep(intervalo)
        transcurrido += intervalo

    return False


async def _esperar_turnstile_resuelto(page, config) -> bool:
    """
    Espera a que el usuario resuelva manualmente Turnstile.

    La comprobación se basa en la respuesta del widget; nunca pulsa
    ni intenta resolver automáticamente la casilla de Cloudflare.
    """
    configuracion = config["turnstile"]

    if not configuracion["habilitado"]:
        logger.warning(
            "Turnstile está deshabilitado en la configuración."
        )
        return False

    tiempo_maximo = configuracion["tiempo_maximo_espera"]
    intervalo = configuracion["intervalo_verificacion"]
    transcurrido = 0.0
    aviso_mostrado = False

    logger.info(
        "Resuelve manualmente la casilla de Cloudflare en el navegador."
    )

    while transcurrido < tiempo_maximo:
        estado = await _estado_turnstile(page, config)

        if estado.get("respuesta_disponible", False):
            logger.info(
                "Se detectó una respuesta de Turnstile. "
                "Se continuará con el formulario."
            )
            return True

        if (
            estado.get("iframe_visible", False)
            and not aviso_mostrado
        ):
            logger.info(
                "El desafío de Cloudflare está visible. "
                "Esperando la intervención manual."
            )
            aviso_mostrado = True

        await asyncio.sleep(intervalo)
        transcurrido += intervalo

    logger.warning(
        "Turnstile no fue confirmado dentro de los {} segundos.",
        tiempo_maximo,
    )
    return False


def cargar_configuracion() -> dict[str, Any]:
    ruta_env = obtener_ruta_env()
    if ruta_env.is_file():
        valores_env = dotenv_values(dotenv_path=ruta_env)
    else:
        valores_env = {}
    def obtener_texto(clave: str, predeterminado: str) -> str:
        valor = os.environ.get(clave)
        if valor is None:
            valor = valores_env.get(clave)
        if valor is None:
            return predeterminado
        return str(valor).strip()
    def obtener_entero(clave: str, predeterminado: int) -> int:
        valor = obtener_texto(clave, str(predeterminado))
        try:
            return int(valor)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Configuración inválida para {clave}: {valor!r}. Se esperaba un número entero.") from exc
    def obtener_decimal(clave: str, predeterminado: float) -> float:
        valor = obtener_texto(clave, str(predeterminado))
        try:
            return float(valor)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Configuración inválida para {clave}: {valor!r}. Se esperaba un número.") from exc
    def obtener_booleano(clave: str, predeterminado: bool) -> bool:
        valor = obtener_texto(clave, "true" if predeterminado else "false").lower()
        if valor in {"true", "1", "yes", "si", "sí", "on"}:
            return True
        if valor in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"Configuración inválida para {clave}: {valor!r}. Use true o false.")
    return {
        "_ruta_env": ruta_env,
        "url_inicio": obtener_texto("SUNARP_URL_INICIO", "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/inicio"),
        "url_autenticado": obtener_texto("SUNARP_URL_AUTENTICADO", "https://conoce-aqui.sunarp.gob.pe/conoce-aqui/servicio/busqueda"),
        "archivos": {"credenciales": obtener_texto("SUNARP_CREDENCIALES_FILE", "credenciales.json")},
        "espera": {
            "carga_pagina": obtener_entero("SUNARP_ESPERA_CARGA_PAGINA", 30),
            "modal_terminos": obtener_entero("SUNARP_ESPERA_MODAL_TERMINOS", 20),
            "desaparicion_modal": obtener_entero("SUNARP_ESPERA_DESAPARICION_MODAL", 15),
            "formulario": obtener_entero("SUNARP_ESPERA_FORMULARIO", 20),
            "resultado_login": obtener_entero("SUNARP_ESPERA_RESULTADO_LOGIN", 60),
            "sondeo": obtener_decimal("SUNARP_INTERVALO_SONDEO", 0.5),
        },
        "escritura": {
            "pausa_minima": obtener_decimal("SUNARP_PAUSA_MINIMA", 0.08),
            "pausa_maxima": obtener_decimal("SUNARP_PAUSA_MAXIMA", 0.22),
            "pausa_entre_campos": obtener_decimal("SUNARP_PAUSA_ENTRE_CAMPOS", 0.5),
        },
        "selectores": {
            "modal_terminos": obtener_texto("SUNARP_SELECTOR_MODAL_TERMINOS", ".ant-modal-content"),
            "aceptar_terminos": obtener_texto("SUNARP_SELECTOR_ACEPTAR_TERMINOS", "button.accept-button"),
            "dni": obtener_texto("SUNARP_SELECTOR_DNI", 'input[formcontrolname="numeroDocumento"]'),
            "digito_verificacion": obtener_texto("SUNARP_SELECTOR_DIGITO", 'input[formcontrolname="digito"]'),
            "fecha_emision": obtener_texto("SUNARP_SELECTOR_FECHA_EMISION", 'input[formcontrolname="fechaEmision"]'),
            "validar": obtener_texto("SUNARP_SELECTOR_VALIDAR", "button.login-form-button"),
        },
        "turnstile": {
            "habilitado": obtener_booleano("SUNARP_TURNSTILE_HABILITADO", True),
            "iframe_host": obtener_texto("SUNARP_TURNSTILE_IFRAME_HOST", "challenges.cloudflare.com"),
            "selector_componente": obtener_texto("SUNARP_TURNSTILE_SELECTOR_COMPONENTE", "cloudcaptcha ngx-turnstile"),
            "selector_respuesta": obtener_texto("SUNARP_TURNSTILE_SELECTOR_RESPUESTA", 'input[name="cf-turnstile-response"]'),
            "tiempo_deteccion_inicial": obtener_decimal("SUNARP_TURNSTILE_TIEMPO_DETECCION_INICIAL", 5.0),
            "tiempo_maximo_espera": obtener_entero("SUNARP_TURNSTILE_TIEMPO_MAXIMO_ESPERA", 180),
            "intervalo_verificacion": obtener_decimal("SUNARP_TURNSTILE_INTERVALO_VERIFICACION", 0.5),
        },
        "limites": {
            "ingresos_maximos_diarios": obtener_entero("SUNARP_INGRESOS_MAXIMOS_DIARIOS", 5),
            "duracion_bloqueo_horas": obtener_entero("SUNARP_DURACION_BLOQUEO_HORAS", 24),
        },
        "comportamiento": {},
        "logging": {
            "nivel": obtener_texto("SUNARP_LOG_NIVEL", "INFO"),
            "formato": obtener_texto("SUNARP_LOG_FORMATO", "%(asctime)s | %(levelname)s | %(message)s"),
        },
    }

def validar_configuracion(config: dict[str, Any]) -> None:
    espera = config["espera"]
    escritura = config["escritura"]
    turnstile = config["turnstile"]
    limites = config["limites"]
    tiempos_positivos = {
        "SUNARP_ESPERA_CARGA_PAGINA": espera["carga_pagina"],
        "SUNARP_ESPERA_MODAL_TERMINOS": espera["modal_terminos"],
        "SUNARP_ESPERA_DESAPARICION_MODAL": espera["desaparicion_modal"],
        "SUNARP_ESPERA_FORMULARIO": espera["formulario"],
        "SUNARP_ESPERA_RESULTADO_LOGIN": espera["resultado_login"],
        "SUNARP_INTERVALO_SONDEO": espera["sondeo"],
        "SUNARP_PAUSA_MINIMA": escritura["pausa_minima"],
        "SUNARP_PAUSA_MAXIMA": escritura["pausa_maxima"],
        "SUNARP_TURNTSTILE_INTERVALO_VERIFICACION": turnstile["intervalo_verificacion"],
    }
    for nombre, valor in tiempos_positivos.items():
        if valor <= 0:
            raise ValueError(f"{nombre} debe ser mayor que cero.")
    if escritura["pausa_minima"] > escritura["pausa_maxima"]:
        raise ValueError("SUNARP_PAUSA_MINIMA no puede superar SUNARP_PAUSA_MAXIMA.")
    if escritura["pausa_entre_campos"] < 0:
        raise ValueError("SUNARP_PAUSA_ENTRE_CAMPOS no puede ser negativo.")
    if turnstile["tiempo_deteccion_inicial"] < 0:
        raise ValueError("SUNARP_TURNTSTILE_TIEMPO_DETECCION_INICIAL no puede ser negativo.")
    if turnstile["tiempo_maximo_espera"] <= 0:
        raise ValueError("SUNARP_TURNTSTILE_TIEMPO_MAXIMO_ESPERA debe ser mayor que cero.")
    if limites["ingresos_maximos_diarios"] <= 0:
        raise ValueError("SUNARP_INGRESOS_MAXIMOS_DIARIOS debe ser mayor que cero.")
    if limites["duracion_bloqueo_horas"] <= 0:
        raise ValueError("SUNARP_DURACION_BLOQUEO_HORAS debe ser mayor que cero.")
    for nombre in ("url_inicio", "url_autenticado"):
        parsed = urlparse(config[nombre])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"La configuración {nombre} debe ser una URL HTTPS válida.")
    for nombre in ("modal_terminos", "aceptar_terminos", "dni", "digito_verificacion", "fecha_emision", "validar"):
        if not config["selectores"][nombre].strip():
            raise ValueError(f"El selector '{nombre}' no puede estar vacío.")
    if turnstile["habilitado"]:
        for nombre in ("iframe_host", "selector_componente", "selector_respuesta"):
            if not turnstile[nombre].strip():
                raise ValueError(f"La configuración Turnstile '{nombre}' no puede estar vacía.")

def obtener_ruta_credenciales(config: dict[str, Any]) -> Path:
    ruta = Path(config["archivos"]["credenciales"]).expanduser()

    if ruta.is_absolute():
        return ruta.resolve()

    ruta_env = Path(config["_ruta_env"])
    return (ruta_env.parent / ruta).resolve()

def cargar_credenciales(ruta: Path) -> list[dict[str, Any]]:
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo de credenciales: {ruta}")
    with ruta.open("r", encoding="utf-8") as archivo:
        datos = json.load(archivo)
    if not isinstance(datos, list) or not datos:
        raise ValueError("credenciales.json debe contener una lista no vacía.")
    campos_obligatorios = ("dni", "digito", "fecha_emision")
    for indice, registro in enumerate(datos, start=1):
        if not isinstance(registro, dict):
            raise ValueError(f"El registro {indice} de credenciales.json debe ser un objeto.")
        for campo in campos_obligatorios:
            if not str(registro.get(campo, "")).strip():
                raise ValueError(f"Falta el campo obligatorio '{campo}' en el registro {indice} de credenciales.json.")
        dni = str(registro["dni"]).strip()
        digito = str(registro["digito"]).strip()
        if len(dni) != 8 or not dni.isdigit():
            raise ValueError(f"El DNI del registro {indice} debe contener exactamente ocho dígitos.")
        if len(digito) != 1 or not digito.isdigit():
            raise ValueError(f"El dígito de verificación del registro {indice} debe contener exactamente un dígito.")
        normalizar_fecha_emision(registro["fecha_emision"])
    return datos

def guardar_credenciales(ruta: Path, credenciales: list[dict[str, Any]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp", delete=False) as archivo:
            temporal = Path(archivo.name)
            json.dump(credenciales, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
    finally:
        if temporal is not None and temporal.exists():
            temporal.unlink(missing_ok=True)

def preparar_ciclo(registro: dict[str, Any], ruta: Path, credenciales: list[dict[str, Any]], config: dict[str, Any]) -> str:
    ahora = time.time()
    hoy = date.today().isoformat()
    bloqueo_hasta = registro.get("bloqueado_hasta")
    if bloqueo_hasta is not None:
        try:
            vencimiento = float(bloqueo_hasta)
        except (TypeError, ValueError) as exc:
            raise ValueError("El campo 'bloqueado_hasta' debe ser un timestamp Unix.") from exc
        if ahora < vencimiento:
            return "bloqueado"
        registro.pop("bloqueado_hasta", None)
        registro["ingresos_ciclo"] = 0
        registro["timestamp_primer_ingreso"] = None
        registro["fecha_ciclo"] = hoy
        guardar_credenciales(ruta, credenciales)
    elif registro.get("fecha_ciclo") != hoy:
        registro["ingresos_ciclo"] = 0
        registro["timestamp_primer_ingreso"] = None
        registro["fecha_ciclo"] = hoy
        guardar_credenciales(ruta, credenciales)
    maximo = int(config["limites"]["ingresos_maximos_diarios"])
    ingresos = int(registro.get("ingresos_ciclo", 0))
    if ingresos >= maximo:
        return "limite"
    return "disponible"

def registrar_ingreso_confirmado(registro: dict[str, Any], ruta: Path, credenciales: list[dict[str, Any]]) -> None:
    ahora = time.time()
    registro["ingresos_ciclo"] = int(registro.get("ingresos_ciclo", 0)) + 1
    if registro.get("timestamp_primer_ingreso") is None:
        registro["timestamp_primer_ingreso"] = ahora
    registro["fecha_ciclo"] = date.today().isoformat()
    guardar_credenciales(ruta, credenciales)
    logger.info("Acceso confirmado. Ingresos registrados en el ciclo: %s", registro["ingresos_ciclo"])

def registrar_bloqueo(registro: dict[str, Any], ruta: Path, credenciales: list[dict[str, Any]], config: dict[str, Any]) -> None:
    horas = float(config["limites"]["duracion_bloqueo_horas"])
    registro["bloqueado_hasta"] = time.time() + horas * 3600
    guardar_credenciales(ruta, credenciales)
    logger.warning("Bloqueo oficial detectado. Se procederá a la rotación.")

async def evaluar(page: Any, expresion: str) -> Any:
    resultado = await page.evaluate(expresion, return_by_value=True)
    return normalizar_resultado_nodriver(resultado)

def normalizar_resultado_nodriver(resultado: Any) -> Any:
    if resultado is None or isinstance(resultado, (str, int, float, bool)):
        return resultado
    if isinstance(resultado, dict):
        if "type" in resultado and "value" in resultado:
            return normalizar_resultado_nodriver(resultado["value"])
        return {clave: normalizar_resultado_nodriver(valor) for clave, valor in resultado.items()}
    serializado = getattr(resultado, "deep_serialized_value", None)
    if serializado is not None:
        return normalizar_resultado_nodriver(serializado.value)
    if getattr(resultado, "type_", None) == "undefined":
        return None
    if isinstance(resultado, (list, tuple)):
        if all(isinstance(item, (list, tuple)) and len(item) == 2 for item in resultado):
            return {str(clave): normalizar_resultado_nodriver(valor) for clave, valor in resultado}
        return [normalizar_resultado_nodriver(item) for item in resultado]
    return resultado

async def _estado_turnstile(page: uc.Tab, config: dict[str, Any]) -> dict[str, bool]:
    turnstile = config["turnstile"]
    componente_json = json.dumps(turnstile["selector_componente"])
    respuesta_json = json.dumps(turnstile["selector_respuesta"])
    host_json = json.dumps(turnstile.get("iframe_host", "challenges.cloudflare.com"))
    script = f"""
    (() => {{
        const componente = document.querySelector({componente_json});
        const respuesta = document.querySelector({respuesta_json});
        const host = {host_json};
        const visible = (elemento) => {{
            if (!elemento) return false;
            const estilo = window.getComputedStyle(elemento);
            const rect = elemento.getBoundingClientRect();
            return (
                estilo.display !== "none" &&
                estilo.visibility !== "hidden" &&
                Number(estilo.opacity) !== 0 &&
                rect.width > 0 &&
                rect.height > 0
            );
        }};
        const iframes = Array.from(document.querySelectorAll("iframe")).filter((iframe) => {{
            const src = iframe.getAttribute("src") || "";
            return src.includes(host);
        }});
        const valor = (respuesta && typeof respuesta.value === "string") ? respuesta.value.trim() : "";
        return JSON.stringify({{
            componente_presente: Boolean(componente),
            respuesta_presente: Boolean(respuesta),
            respuesta_disponible: valor.length > 0,
            iframe_presente: iframes.length > 0,
            iframe_visible: iframes.some(visible)
        }});
    }})()
    """
    resultado = await evaluar(page, script)
    if isinstance(resultado, str):
        try:
            estado = json.loads(resultado)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Turnstile devolvió un estado JSON inválido.") from exc
    elif isinstance(resultado, dict):
        estado = resultado
    else:
        estado = normalizar_resultado_nodriver(resultado)
        if isinstance(estado, str):
            try:
                estado = json.loads(estado)
            except json.JSONDecodeError as exc:
                raise RuntimeError("No se pudo interpretar el estado de Turnstile.") from exc
    if not isinstance(estado, dict):
        raise RuntimeError("El estado de Turnstile no es un objeto válido.")
    return {
        "componente_presente": bool(estado.get("componente_presente")),
        "respuesta_presente": bool(estado.get("respuesta_presente")),
        "respuesta_disponible": bool(estado.get("respuesta_disponible")),
        "iframe_presente": bool(estado.get("iframe_presente")),
        "iframe_visible": bool(estado.get("iframe_visible")),
    }

async def _esperar_verificacion_manual(page: uc.Tab, config: dict[str, Any]) -> bool:
    configuracion = config["turnstile"]
    if not configuracion.get("habilitado", True):
        logger.error("La gestión de Turnstile está deshabilitada en la configuración.")
        return False
    tiempo_maximo = float(configuracion["tiempo_maximo_espera"])
    intervalo = float(configuracion["intervalo_verificacion"])
    tiempo_deteccion = float(configuracion["tiempo_deteccion_inicial"])
    if tiempo_maximo <= 0:
        raise ValueError("'tiempo_maximo_espera' debe ser mayor que cero.")
    if intervalo <= 0:
        raise ValueError("'intervalo_verificacion' debe ser mayor que cero.")
    if tiempo_deteccion < 0:
        raise ValueError("'tiempo_deteccion_inicial' no puede ser negativo.")
    loop = asyncio.get_running_loop()
    limite_deteccion = loop.time() + tiempo_deteccion
    logger.info("Comprobando si SUNARP requiere una verificación manual de Turnstile.")
    while True:
        try:
            estado = await _estado_turnstile(page, config)
            if estado["respuesta_disponible"]:
                logger.info("Turnstile dispone de una respuesta. Se continuará con la comprobación del formulario.")
                return True
            if estado["iframe_visible"]:
                logger.info("Se detectó un desafío visible de Cloudflare. Esperando la intervención manual.")
                break
        except Exception as exc:
            logger.debug("No se pudo consultar temporalmente el estado de Turnstile: %s", exc)
        restante = limite_deteccion - loop.time()
        if restante <= 0:
            logger.info("No se detectó un desafío interactivo de Turnstile durante la ventana inicial. Se continuará con el flujo normal de autenticación.")
            return True
        await asyncio.sleep(min(intervalo, restante))
    limite_resolucion = loop.time() + tiempo_maximo
    while loop.time() < limite_resolucion:
        try:
            estado = await _estado_turnstile(page, config)
            if estado["respuesta_disponible"]:
                logger.info("Se detectó una respuesta de Turnstile después de la intervención manual. Se continuará con el flujo de autenticación.")
                return True
        except Exception as exc:
            logger.debug("No se pudo comprobar temporalmente la resolución de Turnstile: %s", exc)
        restante = limite_resolucion - loop.time()
        if restante > 0:
            await asyncio.sleep(min(intervalo, restante))
    logger.warning("Se agotó el tiempo de espera de Turnstile (%.1f segundos). No se continuará con el envío del formulario.", tiempo_maximo)
    return False

def expresion_elemento_visible(selector: str) -> str:
    selector_js = json.dumps(selector)
    return f"""
        (() => {{
            const elemento = document.querySelector({selector_js});
            if (!elemento) return false;
            const estilo = getComputedStyle(elemento);
            const rect = elemento.getBoundingClientRect();
            return (
                estilo.display !== "none" &&
                estilo.visibility !== "hidden" &&
                rect.width > 0 &&
                rect.height > 0
            );
        }})()
    """

async def esperar_condicion(page: Any, expresion: str, timeout: float, intervalo: float) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            if await evaluar(page, expresion):
                return True
        except Exception:
            logger.debug("La condición aún no puede evaluarse.", exc_info=True)
        await asyncio.sleep(intervalo)
    return False

async def esperar_elemento_visible(page: Any, selector: str, timeout: float, intervalo: float) -> Any | None:
    visible = await esperar_condicion(page, expresion_elemento_visible(selector), timeout, intervalo)
    if not visible:
        return None
    return await page.select(selector)

async def aceptar_terminos(page: Any, config: dict[str, Any]) -> bool:
    espera = config["espera"]
    selectores = config["selectores"]
    intervalo = float(espera["sondeo"])
    selector_modal = selectores["modal_terminos"]
    selector_boton = selectores["aceptar_terminos"]
    boton = await esperar_elemento_visible(page, selector_boton, float(espera["modal_terminos"]), intervalo)
    if boton is not None:
        texto = str(getattr(boton, "text", "") or "").strip().casefold()
        if "sí acepto" not in texto and "si acepto" not in texto:
            logger.error("Se encontró el botón, pero su texto no es 'Sí Acepto'.")
            return False
        await boton.click()
        desaparecio = await esperar_condicion(page, f"!({expresion_elemento_visible(selector_modal)})", float(espera["desaparicion_modal"]), intervalo)
        if not desaparecio:
            logger.error("Se pulsó 'Sí Acepto', pero el modal sigue visible.")
            return False
        logger.info("Términos aceptados correctamente.")
        return True
    modal_visible = await esperar_condicion(page, expresion_elemento_visible(selector_modal), 1.0, intervalo)
    if not modal_visible:
        logger.info("No se detectó el modal ni el botón de aceptación. Se continuará con la comprobación del formulario.")
        return True
    logger.error("El modal está visible, pero no se encontró el botón 'Sí Acepto'.")
    return False

async def esperar_formulario(page: Any, config: dict[str, Any]) -> bool:
    espera = config["espera"]
    selectores = config["selectores"]
    intervalo = float(espera["sondeo"])
    campos = (selectores["dni"], selectores["digito_verificacion"], selectores["fecha_emision"])
    expresiones = []
    for selector in campos:
        selector_js = json.dumps(selector)
        expresiones.append(f"""
            (() => {{
                const e = document.querySelector({selector_js});
                if (!e || e.disabled) return false;
                const estilo = getComputedStyle(e);
                const rect = e.getBoundingClientRect();
                return (
                    estilo.display !== "none" &&
                    estilo.visibility !== "hidden" &&
                    rect.width > 0 &&
                    rect.height > 0
                );
            }})()
        """)
    selector_boton_js = json.dumps(selectores["validar"])
    expresiones.append(f"""
        (() => {{
            const e = document.querySelector({selector_boton_js});
            if (!e) return false;
            const estilo = getComputedStyle(e);
            const rect = e.getBoundingClientRect();
            return (
                estilo.display !== "none" &&
                estilo.visibility !== "hidden" &&
                rect.width > 0 &&
                rect.height > 0
            );
        }})()
    """)
    expresion = " && ".join(f"({item})" for item in expresiones)
    disponible = await esperar_condicion(page, expresion, float(espera["formulario"]), intervalo)
    if not disponible:
        logger.error("El formulario no estuvo disponible a tiempo.")
        return False
    logger.info("Formulario disponible: los tres campos están visibles y habilitados; el botón Validar está visible.")
    return True

def normalizar_fecha_emision(valor: Any) -> str:
    texto = str(valor).strip()
    if "/" in texto:
        partes = texto.split("/")
        if len(partes) != 3:
            raise ValueError("Formato de fecha de emisión no válido.")
        dia, mes, anio = partes
    else:
        digitos = "".join(c for c in texto if c.isdigit())
        if len(digitos) != 8:
            raise ValueError("La fecha de emisión debe tener ocho dígitos o el formato DD/MM/AAAA.")
        dia, mes, anio = digitos[:2], digitos[2:4], digitos[4:]
    from datetime import datetime
    fecha = datetime.strptime(f"{dia}/{mes}/{anio}", "%d/%m/%Y")
    return fecha.strftime("%d/%m/%Y")

async def escribir_gradualmente(page: Any, selector: str, valor: str, pausa_minima: float, pausa_maxima: float) -> bool:
    elemento = None
    try:
        logger.info("Buscando el control: %s", selector)
        elemento = await page.select(selector)
        if elemento is None:
            logger.error("No se encontró el control: %s", selector)
            return False
        await elemento.click()
        await asyncio.sleep(0.2)
        logger.info("Control enfocado; iniciando escritura (%s caracteres).", len(valor))
        for indice, caracter in enumerate(valor, start=1):
            await elemento.send_keys(caracter)
            await asyncio.sleep(random.uniform(pausa_minima, pausa_maxima))
            logger.debug("Escritura: carácter %s de %s.", indice, len(valor))
        selector_js = json.dumps(selector)
        expresion = f"""
            (() => {{
                const e = document.querySelector({selector_js});
                if (!e) {{
                    return {{
                        encontrado: false,
                        valor: null
                    }};
                }}
                return {{
                    encontrado: true,
                    valor: e.value,
                    longitud: e.value.length,
                    enfocado: document.activeElement === e
                }};
            }})()
        """
        resultado = await evaluar(page, expresion)
        if isinstance(resultado, dict):
            logger.info("Verificación del control %s: encontrado=%s, longitud=%s, enfocado=%s.", selector, resultado.get("encontrado"), resultado.get("longitud"), resultado.get("enfocado"))
        else:
            logger.warning("Respuesta de verificación inesperada para el control %s: tipo=%s.", selector, type(resultado).__name__)
        if not isinstance(resultado, dict):
            logger.error("Respuesta inesperada de Nodriver al verificar %s.", selector)
            return False
        correcto = (resultado.get("encontrado") is True and resultado.get("valor") == valor)
        if not correcto:
            logger.error("La verificación de %s falló. Longitud esperada=%s; longitud obtenida=%s.", selector, len(valor), resultado.get("longitud"))
            return False
        logger.info("Control completado y verificado: %s", selector)
        return True
    except Exception:
        logger.exception("Excepción durante la escritura o verificación de %s.", selector)
        return False

async def detectar_bloqueo(page: Any) -> bool:
    mensaje_js = json.dumps(MENSAJE_BLOQUEO)
    return bool(await evaluar(page, f"""
            (() => {{
                const texto = document.body?.innerText || "";
                return texto.includes({mensaje_js});
            }})()
            """))

async def pulsar_aceptar_bloqueo(page: Any) -> None:
    expresion = """
        (() => {
            const botones = [...document.querySelectorAll("button")];
            const boton = botones.find(
                e => (e.innerText || "").trim().toLowerCase() === "aceptar"
            );
            if (!boton) return false;
            const estilo = getComputedStyle(boton);
            const rect = boton.getBoundingClientRect();
            if (
                estilo.display === "none" ||
                estilo.visibility === "hidden" ||
                rect.width === 0 ||
                rect.height === 0
            ) {
                return false;
            }
            boton.click();
            return true;
        })()
    """
    try:
        await evaluar(page, expresion)
    except Exception:
        pass

async def limpiar_y_verificar_campos(page: Any, config: dict[str, Any]) -> bool:
    selectores = config["selectores"]
    selectores_js = json.dumps([selectores["dni"], selectores["digito_verificacion"], selectores["fecha_emision"]])
    expresion = f"""
        (() => {{
            const selectores = {selectores_js};
            for (const selector of selectores) {{
                const e = document.querySelector(selector);
                if (!e) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype,
                    "value"
                ).set;
                setter.call(e, "");
                e.dispatchEvent(new Event("input", {{ bubbles: true }}));
                e.dispatchEvent(new Event("change", {{ bubbles: true }}));
                e.dispatchEvent(new Event("blur", {{ bubbles: true }}));
            }}
            return selectores.every(selector => {{
                const e = document.querySelector(selector);
                return e && e.value === "";
            }});
        }})()
    """
    return bool(await evaluar(page, expresion))

async def obtener_url_actual(page: Any) -> str:
    try:
        resultado = await evaluar(page, "window.location.href")
        if isinstance(resultado, str) and resultado.strip():
            return resultado.strip()
        logger.warning("No se obtuvo una URL válida desde window.location.href. Tipo recibido: %s; valor recibido: %r", type(resultado).__name__, resultado)
    except Exception:
        logger.exception("No se pudo obtener la URL actual mediante JavaScript.")
    try:
        url = str(page.url or "").strip()
        if url:
            return url
    except Exception:
        logger.exception("Tampoco se pudo obtener la URL mediante page.url.")
    return ""

async def resolver_dialogo_sesion_activa(page: Any) -> str:
    expresion = r"""
    (() => {
        const normalizar = (texto) =>
            (texto || "")
                .normalize("NFD")
                .replace(/[\u0300-\u036f]/g, "")
                .replace(/\s+/g, " ")
                .trim()
                .toLowerCase();
        const dialogo = document.querySelector("div.swal2-popup.swal2-show");
        if (!dialogo) {
            return {
                detectado: false,
                pulsado: false
            };
        }
        const titulo = normalizar(
            dialogo.querySelector("#swal2-title")?.innerText
        );
        const descripcion = normalizar(
            dialogo.querySelector("#swal2-html-container")?.innerText
        );
        const esSesionActiva =
            titulo.includes(
                "en este momento hay una sesion activa asociada al documento ingresado"
            ) &&
            descripcion.includes(
                "se cerrara automaticamente la sesion que actualmente esta activa"
            );
        if (!esSesionActiva) {
            return {
                detectado: false,
                pulsado: false
            };
        }
        const boton = dialogo.querySelector("button.swal2-confirm");
        if (!boton) {
            return {
                detectado: true,
                pulsado: false,
                motivo: "No se encontró button.swal2-confirm."
            };
        }
        const textoBoton = normalizar(
            boton.innerText || boton.textContent
        );
        const estilo = window.getComputedStyle(boton);
        const rect = boton.getBoundingClientRect();
        const visible =
            estilo.display !== "none" &&
            estilo.visibility !== "hidden" &&
            Number(estilo.opacity) !== 0 &&
            rect.width > 0 &&
            rect.height > 0;
        const habilitado =
            !boton.disabled &&
            boton.getAttribute("aria-disabled") !== "true";
        if (textoBoton !== "si, iniciar") {
            return {
                detectado: true,
                pulsado: false,
                motivo: "El texto del botón no coincide."
            };
        }
        if (!visible || !habilitado) {
            return {
                detectado: true,
                pulsado: false,
                motivo: "El botón no está visible o habilitado."
            };
        }
        boton.click();
        return {
            detectado: true,
            pulsado: true
        };
    })()
    """
    try:
        resultado = await evaluar(page, expresion)
        if not isinstance(resultado, dict):
            logger.error("Respuesta inesperada al inspeccionar el diálogo de sesión activa: %r", resultado)
            return "error"
        if not resultado.get("detectado"):
            return "no_detectado"
        if resultado.get("pulsado"):
            logger.warning("SUNARP mostró el diálogo de sesión activa. Se pulsó «Sí, iniciar».")
            return "resuelto"
        logger.error("Se detectó el diálogo de sesión activa, pero no se pudo pulsar «Sí, iniciar». Detalle: %s", resultado.get("motivo", "Motivo no especificado."))
        return "error"
    except Exception:
        logger.exception("Error al detectar o resolver el diálogo de sesión activa.")
        return "error"


async def resolver_dialogo_conocido(page: Any) -> str:
    expresion = r"""
    (() => {
        const normalizar = (texto) =>
            (texto || "")
                .normalize("NFD")
                .replace(/[\u0300-\u036f]/g, "")
                .replace(/\s+/g, " ")
                .trim()
                .toLowerCase();

        const visible = (elemento) => {
            if (!elemento) return false;

            const estilo = getComputedStyle(elemento);
            const rect = elemento.getBoundingClientRect();

            return estilo.display !== "none"
                && estilo.visibility !== "hidden"
                && Number(estilo.opacity) !== 0
                && rect.width > 0
                && rect.height > 0;
        };

        const dialogos = [
            ...document.querySelectorAll(".swal2-popup.swal2-show")
        ].filter(visible);

        return JSON.stringify(dialogos.map((dialogo) => {
            const titulo = normalizar(
                dialogo.querySelector("#swal2-title")?.innerText
            );

            const descripcion = normalizar(
                dialogo.querySelector("#swal2-html-container")?.innerText
            );

            const botones = [
                ...dialogo.querySelectorAll("button")
            ]
            .filter(visible)
            .map((boton) => ({
                texto: normalizar(
                    boton.innerText || boton.textContent
                ),
                habilitado: !boton.disabled
                    && boton.getAttribute("aria-disabled") !== "true"
            }));

            return {
                titulo,
                descripcion,
                texto: normalizar(dialogo.innerText),
                botones
            };
        }));
    })()
    """

    try:
        respuesta = await evaluar(page, expresion)

        if not isinstance(respuesta, str):
            logger.error(
                "El detector de modales recibió un tipo inesperado: %s.",
                type(respuesta).__name__,
            )
            return "error"

        try:
            dialogos = json.loads(respuesta)
        except json.JSONDecodeError:
            logger.error(
                "Nodriver devolvió una respuesta que no es JSON válido "
                "al inspeccionar los modales."
            )
            return "error"

        if not isinstance(dialogos, list):
            logger.error(
                "La respuesta JSON del detector no contiene una lista."
            )
            return "error"

        for dialogo in dialogos:
            if not isinstance(dialogo, dict):
                continue

            titulo = str(dialogo.get("titulo", ""))
            descripcion = str(dialogo.get("descripcion", ""))

            # Mantener el reconocimiento de la sesión activa.
            es_sesion_activa = (
                "en este momento hay una sesion activa asociada "
                "al documento ingresado" in titulo
                and
                "se cerrara automaticamente la sesion que actualmente "
                "esta activa" in descripcion
            )

            if es_sesion_activa:
                resultado = await resolver_dialogo_sesion_activa(page)

                if resultado == "resuelto":
                    return "resuelto"

                logger.error(
                    "Se identificó la sesión activa, pero su "
                    "resolución devolvió %r.",
                    resultado,
                )
                return "error"

            # Compatibilidad con el mensaje anterior y el mensaje actual.
            limite_anterior = (
                "ha alcanzado la cantidad maxima permitida de "
                "5 ingresos al sistema" in titulo
                and
                "manana podra volver a utilizar nuestro servicio "
                "gratuito conoce aqui" in descripcion
            )

            limite_actual = (
                "ha excedido el limite de 5 consultas gratuitas "
                "permitidas" in titulo
                and
                "el dia de manana podra realizar 5 nuevas consultas"
                in descripcion
            )

            if limite_anterior or limite_actual:
                logger.warning(
                    "SUNARP confirmó el límite diario de la credencial."
                )

                # La detección positiva del límite es suficiente para
                # clasificar la credencial como bloqueada por hoy.
                # El clic es una acción de limpieza, no una condición
                # para reconocer el límite.
                expresion_aceptar = r"""
                (() => {
                    const normalizar = (texto) =>
                        (texto || "")
                            .normalize("NFD")
                            .replace(/[\u0300-\u036f]/g, "")
                            .replace(/\s+/g, " ")
                            .trim()
                            .toLowerCase();

                    const visible = (elemento) => {
                        if (!elemento) return false;

                        const estilo = getComputedStyle(elemento);
                        const rect = elemento.getBoundingClientRect();

                        return estilo.display !== "none"
                            && estilo.visibility !== "hidden"
                            && Number(estilo.opacity) !== 0
                            && rect.width > 0
                            && rect.height > 0;
                    };

                    const dialogos = [
                        ...document.querySelectorAll(
                            ".swal2-popup.swal2-show"
                        )
                    ].filter(visible);

                    for (const dialogo of dialogos) {
                        const titulo = normalizar(
                            dialogo.querySelector("#swal2-title")?.innerText
                        );

                        const descripcion = normalizar(
                            dialogo.querySelector(
                                "#swal2-html-container"
                            )?.innerText
                        );

                        const anterior = (
                            titulo.includes(
                                "ha alcanzado la cantidad maxima "
                                + "permitida de 5 ingresos al sistema"
                            )
                            && descripcion.includes(
                                "manana podra volver a utilizar "
                                + "nuestro servicio gratuito conoce aqui"
                            )
                        );

                        const actual = (
                            titulo.includes(
                                "ha excedido el limite de 5 consultas "
                                + "gratuitas permitidas"
                            )
                            && descripcion.includes(
                                "el dia de manana podra realizar "
                                + "5 nuevas consultas"
                            )
                        );

                        if (!anterior && !actual) {
                            continue;
                        }

                        const boton = [
                            ...dialogo.querySelectorAll(
                                "button.swal2-confirm"
                            )
                        ].find((elemento) =>
                            visible(elemento)
                            && !elemento.disabled
                            && elemento.getAttribute(
                                "aria-disabled"
                            ) !== "true"
                            && normalizar(
                                elemento.innerText || elemento.textContent
                            ) === "aceptar"
                        );

                        if (!boton) {
                            return false;
                        }

                        boton.click();
                        return true;
                    }

                    return false;
                })()
                """

                try:
                    pulsado = await evaluar(page, expresion_aceptar)

                    if pulsado is True:
                        logger.info(
                            "Se pulsó Aceptar en el modal de límite diario."
                        )
                    else:
                        logger.warning(
                            "El límite diario está confirmado, pero no "
                            "se pudo confirmar el clic en Aceptar."
                        )

                except Exception:
                    logger.warning(
                        "El límite diario ya estaba confirmado, pero "
                        "falló la interacción con el botón Aceptar. "
                        "Se conservará la clasificación de límite."
                    )
                    logger.debug(
                        "Detalle del error al cerrar el modal:",
                        exc_info=True,
                    )

                return "bloqueado"

        if dialogos:
            logger.debug(
                "Hay diálogos visibles, pero ninguno coincide "
                "con los patrones registrados."
            )

        return "no_detectado"

    except Exception:
        logger.exception(
            "Error al inspeccionar o resolver los diálogos SUNARP."
        )
        return "error"


async def esperar_resultado(page: Any, url_anterior: str, config: dict[str, Any]) -> str:
    espera = config["espera"]
    intervalo = float(espera["sondeo"])
    limite = time.monotonic() + float(espera["resultado_login"])
    url_objetivo = urlparse(config["url_autenticado"])
    while time.monotonic() < limite:
        try:
            estado_modal = await resolver_dialogo_conocido(page)
            if estado_modal == "bloqueado":
                return "bloqueado"
            if estado_modal == "error":
                return "incierto"
            if estado_modal == "resuelto":
                await asyncio.sleep(intervalo)
                continue
            if await detectar_bloqueo(page):
                logger.warning("SUNARP mostró el mensaje oficial de bloqueo.")
                return "bloqueado"
            url_actual = await obtener_url_actual(page)
            if url_actual:
                actual = urlparse(url_actual)
                ruta_correcta = (
                    actual.scheme.lower() == url_objetivo.scheme.lower()
                    and actual.netloc.lower() == url_objetivo.netloc.lower()
                    and actual.path.rstrip("/") == url_objetivo.path.rstrip("/")
                )
                if ruta_correcta:
                    logger.info("Acceso confirmado por la URL de destino: %s", actual.path)
                    return "autenticado"
        except Exception:
            logger.exception("Error temporal al comprobar el resultado del inicio de sesión; se continuará esperando.")
        await asyncio.sleep(intervalo)
    url_final = await obtener_url_actual(page)
    logger.warning("Tiempo agotado esperando la confirmación del inicio de sesión. URL anterior: %s; URL final: %r.", urlparse(url_anterior).path, url_final)
    return "incierto"

async def cerrar_navegador(browser: Any) -> None:
    logger.warning("DIAGNÓSTICO CIERRE INICIAL | browser_id=%s | proceso_id=%s | stopped=%s", id(browser), id(getattr(browser, "_process", None)), getattr(browser, "stopped", None))
    proceso = getattr(browser, "_process", None)
    try:
        await browser.aclose()
    except Exception:
        logger.debug("Error al cerrar la conexión de Nodriver.", exc_info=True)
    if proceso is None:
        return
    try:
        if proceso.returncode is None:
            proceso.terminate()
    except ProcessLookupError:
        pass
    except Exception:
        logger.warning("No se pudo solicitar la terminación normal del navegador.", exc_info=True)
    try:
        await asyncio.wait_for(proceso.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("El navegador no terminó a tiempo; se forzará su cierre.")
        try:
            proceso.kill()
        except ProcessLookupError:
            pass
        await proceso.wait()
        logger.warning("DIAGNÓSTICO PROCESO FINAL | pid=%s | returncode=%s | browser_stopped=%s", proceso.pid, proceso.returncode, getattr(browser, "stopped", None))
    await asyncio.sleep(0)
    proceso_final = getattr(browser, "_process", None)
    try:
        from nodriver.core import util
        registrado = browser in util.get_registered_instances()
    except Exception:
        registrado = None
    logger.warning("DIAGNÓSTICO CIERRE | pid=%s | returncode=%s | browser_stopped=%s | registrado_en_nodriver=%s", getattr(proceso_final, "pid", None), getattr(proceso_final, "returncode", None), getattr(browser, "stopped", None), registrado)


async def iniciar_sesion_sunarp() -> SunarpSession | bool:
    logger.warning("MÓDULO DE SESIÓN EJECUTADO: %s", __file__)

    config = cargar_configuracion()
    validar_configuracion(config)

    ruta_json = obtener_ruta_credenciales(config)
    credenciales = cargar_credenciales(ruta_json)

    espera = config["espera"]
    escritura = config["escritura"]
    selectores = config["selectores"]

    intervalo = float(espera["sondeo"])
    pausa_min = float(escritura["pausa_minima"])
    pausa_max = float(escritura["pausa_maxima"])

    total = len(credenciales)
    disponibles = 0

    logger.info(
        "Inicio de rotación de credenciales SUNARP. Credenciales registradas: %s.",
        total,
    )

    for indice, registro in enumerate(credenciales):
        numero = indice + 1
        browser = None
        sesion_entregada = False

        try:
            # 1. Comprobar si la credencial puede utilizarse.
            estado = preparar_ciclo(
                registro,
                ruta_json,
                credenciales,
                config,
            )

            if estado == "limite":
                logger.info(
                    "Credencial %s/%s omitida: alcanzó el límite diario de ingresos.",
                    numero,
                    total,
                )
                continue

            if estado == "bloqueado":
                logger.info(
                    "Credencial %s/%s omitida: tiene un bloqueo vigente de SUNARP.",
                    numero,
                    total,
                )
                continue

            if estado != "disponible":
                logger.error(
                    "Estado inesperado para la credencial %s/%s: %r.",
                    numero,
                    total,
                    estado,
                )
                return False

            disponibles += 1

            logger.info(
                "Intentando acceso con la credencial %s/%s.",
                numero,
                total,
            )

            # 2. Abrir SUNARP y esperar la carga.
            browser = await uc.start()

            logger.info(
                "Diagnóstico proceso Nodriver: tipo=%s, pid=%s, poll=%s",
                type(browser._process).__name__,
                getattr(browser._process, "pid", None),
                callable(getattr(browser._process, "poll", None)),
            )

            page = await browser.get(config["url_inicio"])

            pagina_lista = await esperar_condicion(
                page,
                "document.readyState === 'complete' || "
                "document.readyState === 'interactive'",
                float(espera["carga_pagina"]),
                intervalo,
            )

            if not pagina_lista:
                logger.error(
                    "La página de SUNARP no terminó de cargar. "
                    "Se detiene la rotación."
                )
                return False

            # 3. Aceptar los términos y esperar el formulario.
            if not await aceptar_terminos(page, config):
                logger.error(
                    "No se pudieron aceptar los términos de SUNARP. "
                    "Se detiene la rotación."
                )
                return False

            if not await esperar_formulario(page, config):
                logger.error(
                    "El formulario de SUNARP no estuvo disponible. "
                    "Se detiene la rotación."
                )
                return False

            # 4. Preparar y validar los datos de la credencial.
            dni = str(registro["dni"]).strip()
            digito = str(registro["digito"]).strip()
            fecha = normalizar_fecha_emision(
                registro["fecha_emision"]
            )

            if (
                len(dni) != 8
                or not dni.isdigit()
                or len(digito) != 1
                or not digito.isdigit()
            ):
                raise ValueError(
                    f"Datos inválidos en la credencial {numero}."
                )

            campos = (
                (selectores["dni"], dni),
                (selectores["digito_verificacion"], digito),
                (selectores["fecha_emision"], fecha),
            )

            for posicion, (selector, valor) in enumerate(campos):
                correcto = await escribir_gradualmente(
                    page,
                    selector,
                    valor,
                    pausa_min,
                    pausa_max,
                )

                if not correcto:
                    logger.error(
                        "No se pudo completar y verificar un campo "
                        "del formulario para la credencial %s. "
                        "Se detiene la rotación.",
                        numero,
                    )
                    return False

                if posicion < len(campos) - 1:
                    await asyncio.sleep(
                        float(escritura["pausa_entre_campos"])
                    )

            # 5. Comprobar si Turnstile ya fue resuelto.
            #
            # Si existe un desafío visible, se espera la intervención
            # manual antes de enviar el formulario.
            estado_turnstile = await _estado_turnstile(page, config)

            if estado_turnstile["respuesta_disponible"]:
                logger.info(
                    "Turnstile ya dispone de una respuesta."
                )

            elif estado_turnstile["iframe_visible"]:
                verificado = await _esperar_verificacion_manual(
                    page,
                    config,
                )

                if not verificado:
                    logger.warning(
                        "No se confirmó la resolución de Turnstile. "
                        "Se detiene el intento sin probar otras credenciales."
                    )
                    return False

            # 6. Revalidar la disponibilidad antes de enviar.
            estado = preparar_ciclo(
                registro,
                ruta_json,
                credenciales,
                config,
            )

            if estado == "limite":
                logger.info(
                    "La credencial %s alcanzó el límite durante "
                    "la preparación. Se pasa a la siguiente.",
                    numero,
                )
                continue

            if estado == "bloqueado":
                logger.info(
                    "La credencial %s tiene un bloqueo vigente. "
                    "Se pasa a la siguiente.",
                    numero,
                )
                continue

            if estado != "disponible":
                logger.error(
                    "No se pudo confirmar la disponibilidad "
                    "de la credencial %s. Se detiene la rotación.",
                    numero,
                )
                return False

            url_anterior = await obtener_url_actual(page)

            if not url_anterior:
                logger.error(
                    "No se pudo obtener la URL anterior al acceso. "
                    "Se detiene la rotación por resultado incierto."
                )
                return False

            logger.info(
                "Diagnóstico SUNARP: URL antes del clic=%r.",
                url_anterior,
            )

            # 7. Enviar el formulario.
            boton_validar = await page.select(
                selectores["validar"]
            )

            if boton_validar is None:
                logger.error(
                    "No se encontró el botón Validar. "
                    "Se detiene la rotación."
                )
                return False

            await boton_validar.click()

            logger.info(
                "Formulario enviado. Comprobando si SUNARP requiere "
                "una verificación adicional."
            )

            # 8. Esperar el SweetAlert2 que solicita resolver el captcha.
            requiere_captcha = (
                await _cerrar_modal_verificacion_requerida(page)
            )

            if requiere_captcha:
                # El modal es un aviso del portal. Se cierra y se
                # permite al usuario resolver el captcha manualmente.
                verificado = await _esperar_turnstile_resuelto(
                    page,
                    config,
                )

                if not verificado:
                    logger.warning(
                        "No se confirmó la resolución manual de "
                        "Turnstile después del aviso de SUNARP. "
                        "Se detiene el intento sin rotar credenciales."
                    )
                    return False

                # 9. Tras resolver el captcha, volver a enviar
                # el formulario para que SUNARP procese la solicitud.
                url_anterior = await obtener_url_actual(page)

                if not url_anterior:
                    logger.error(
                        "No se pudo obtener la URL antes de reenviar "
                        "el formulario tras resolver el captcha."
                    )
                    return False

                boton_validar = await page.select(
                    selectores["validar"]
                )

                if boton_validar is None:
                    logger.error(
                        "No se encontró el botón Validar después "
                        "de resolver Turnstile."
                    )
                    return False

                await boton_validar.click()

                logger.info(
                    "Formulario reenviado después de resolver "
                    "manualmente Turnstile."
                )

            # 10. Esperar el resultado real de autenticación.
            # La respuesta del captcha por sí sola no acredita
            # que el inicio de sesión haya sido exitoso.
            resultado = await esperar_resultado(
                page,
                url_anterior,
                config,
            )

            if resultado == "autenticado":
                # El contador solo aumenta tras confirmar el acceso.
                registrar_ingreso_confirmado(
                    registro,
                    ruta_json,
                    credenciales,
                )

                logger.info(
                    "Acceso confirmado con la credencial %s/%s.",
                    numero,
                    total,
                )

                sesion = SunarpSession(
                    browser=browser,
                    page=page,
                )

                sesion_entregada = True

                logger.info(
                    "Sesión autenticada transferida al scraper. "
                    "El navegador permanecerá abierto."
                )

                return sesion

            if resultado == "bloqueado":
                registrar_bloqueo(
                    registro,
                    ruta_json,
                    credenciales,
                    config,
                )

                try:
                    campos_limpiados = (
                        await limpiar_y_verificar_campos(
                            page,
                            config,
                        )
                    )

                    if campos_limpiados:
                        logger.info(
                            "Campos del formulario limpiados y "
                            "verificados después del bloqueo."
                        )
                    else:
                        logger.warning(
                            "No se pudo confirmar la limpieza de "
                            "todos los campos después del bloqueo."
                        )

                except Exception:
                    logger.exception(
                        "Error al limpiar el formulario después "
                        "del bloqueo oficial."
                    )

                logger.warning(
                    "SUNARP confirmó el bloqueo de la credencial "
                    "%s/%s. Se continuará con la siguiente.",
                    numero,
                    total,
                )
                continue

            logger.error(
                "Resultado incierto para la credencial %s/%s: %r. "
                "No se incrementará el contador ni se probarán "
                "más credenciales.",
                numero,
                total,
                resultado,
            )
            return False

        except Exception:
            logger.exception(
                "Error durante el intento con la credencial %s/%s. "
                "Se detiene la rotación para evitar interpretar "
                "un fallo técnico como un bloqueo de SUNARP.",
                numero,
                total,
            )
            return False

        finally:
            if browser is not None and not sesion_entregada:
                try:
                    logger.info(
                        "El intento %s/%s no transfirió la sesión. "
                        "Se cerrará el navegador.",
                        numero,
                        total,
                    )
                    await cerrar_navegador(browser)

                except Exception:
                    logger.exception(
                        "Error durante la limpieza del navegador "
                        "del intento %s/%s.",
                        numero,
                        total,
                    )

    if disponibles == 0:
        logger.warning(
            "No hay credenciales disponibles: todas alcanzaron "
            "el límite diario o tienen un bloqueo vigente."
        )
    else:
        logger.warning(
            "Se agotaron las credenciales disponibles sin "
            "confirmar un acceso exitoso."
        )

    return False


async def main() -> None:
    resultado = await iniciar_sesion_sunarp()
    if not isinstance(resultado, SunarpSession):
        logger.error("No se obtuvo una sesión autenticada.")
        return
    try:
        logger.info("Autenticación correcta. Navegador abierto.")
        await asyncio.sleep(2)
    finally:
        await cerrar_navegador(resultado.browser)

if __name__ == "__main__":
    asyncio.run(main())