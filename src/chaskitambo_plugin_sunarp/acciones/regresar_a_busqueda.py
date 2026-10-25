
# Archivo: acciones/regresar_a_busqueda.py

import asyncio
import json
import logging

import nodriver as uc

logger = logging.getLogger(__name__)

URL_BUSQUEDA = "/conoce-aqui/servicio/busqueda"
TIEMPO_MAX_RETORNO = 30
INTERVALO_COMPROBACION = 0.5


async def regresar_a_busqueda(page: uc.Tab) -> bool:
    """Pulsa Regresar y confirma que el formulario de búsqueda esté disponible."""

    script_click = r"""
    (() => {
        const boton = document.querySelector("button.btn-logout");

        if (!boton) {
            return JSON.stringify({
                ok: false,
                motivo: "No se encontró button.btn-logout",
                url: location.href
            });
        }

        const texto = (
            boton.innerText || boton.textContent || ""
        ).replace(/\s+/g, " ").trim().toLowerCase();

        const rect = boton.getBoundingClientRect();
        const estilo = window.getComputedStyle(boton);

        const visible =
            rect.width > 0 &&
            rect.height > 0 &&
            estilo.display !== "none" &&
            estilo.visibility !== "hidden";

        if (!/\bregresar\b/.test(texto)) {
            return JSON.stringify({
                ok: false,
                motivo: "El texto del botón no coincide con Regresar",
                texto,
                url: location.href
            });
        }

        if (!visible || boton.disabled) {
            return JSON.stringify({
                ok: false,
                motivo: "El botón Regresar no está visible o está deshabilitado",
                url: location.href
            });
        }

        boton.click();

        return JSON.stringify({
            ok: true,
            url: location.href
        });
    })()
    """

    try:
        resultado = await page.evaluate(script_click)
    except Exception as exc:
        logger.exception("Error al pulsar Regresar en SUNARP.")
        raise RuntimeError(
            f"No se pudo ejecutar el retorno a búsqueda: {exc}"
        ) from exc

    if not isinstance(resultado, str):
        raise RuntimeError(
            "Respuesta inesperada al pulsar Regresar: "
            f"{type(resultado).__name__}"
        )

    try:
        datos = json.loads(resultado)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "La acción Regresar devolvió una respuesta no válida."
        ) from exc

    if not datos.get("ok"):
        logger.error(
            "No se pudo regresar a búsqueda: %s",
            datos.get("motivo", "motivo desconocido"),
        )
        return False

    tiempo_transcurrido = 0.0

    while tiempo_transcurrido < TIEMPO_MAX_RETORNO:
        try:
            estado = await page.evaluate(
                r"""
                (() => {
                    const rutaCorrecta =
                        location.pathname.includes(
                            "/conoce-aqui/servicio/busqueda"
                        );

                    const campoPartida = document.querySelector(
                        'input[formcontrolname="numero"]'
                    );

                    const selectorCriterio = document.querySelector(
                        'nz-radio-group[formcontrolname="numeroTipo"]'
                    );

                    return JSON.stringify({
                        ruta: location.pathname,
                        rutaCorrecta,
                        formularioDisponible: Boolean(
                            campoPartida && selectorCriterio
                        )
                    });
                })()
                """
            )

            if isinstance(estado, str):
                estado = json.loads(estado)

            if (
                isinstance(estado, dict)
                and estado.get("rutaCorrecta")
                and estado.get("formularioDisponible")
            ):
                logger.info(
                    "Retorno confirmado: formulario de búsqueda SUNARP disponible."
                )
                return True

        except Exception:
            logger.debug(
                "Esperando que SUNARP recupere el formulario de búsqueda."
            )

        await asyncio.sleep(INTERVALO_COMPROBACION)
        tiempo_transcurrido += INTERVALO_COMPROBACION

    logger.error(
        "SUNARP no recuperó el formulario de búsqueda en %s segundos.",
        TIEMPO_MAX_RETORNO,
    )
    return False
