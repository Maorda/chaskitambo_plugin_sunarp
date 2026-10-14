
# Archivo: D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\acciones\pre_extraer_partida\criterio.py

import json
import random

import nodriver as uc


async def configurar_criterio_partida(
    page: uc.Tab,
    numero_partida: str,
) -> None:
    numero = str(numero_partida).strip()

    if not numero:
        raise ValueError("El número de partida está vacío.")

    print(f"[Acción] Configurando criterio para partida: {numero!r}")

    # Seleccionar exclusivamente la opción cuyo texto sea "Partida".
    js_seleccionar_partida = r"""
    (() => {
        const grupos = Array.from(
            document.querySelectorAll(
                'nz-radio-group[formcontrolname="numeroTipo"]'
            )
        );

        for (const grupo of grupos) {
            const etiquetas = Array.from(
                grupo.querySelectorAll('label.ant-radio-wrapper')
            );

            const etiqueta = etiquetas.find((elemento) =>
                (elemento.innerText || "")
                    .replace(/\s+/g, " ")
                    .trim() === "Partida"
            );

            if (!etiqueta) continue;

            const radio = etiqueta.querySelector(
                'input.ant-radio-input[type="radio"]'
            );

            if (!radio) {
                return JSON.stringify({
                    ok: false,
                    motivo: "La opción Partida no contiene un radio button."
                });
            }

            if (!radio.checked) {
                etiqueta.click();
            }

            return JSON.stringify({
                ok: true,
                seleccionado: radio.checked,
                texto: (etiqueta.innerText || "").trim()
            });
        }

        return JSON.stringify({
            ok: false,
            motivo: "No se encontró la opción Partida."
        });
    })()
    """

    resultado = await page.evaluate(
        js_seleccionar_partida,
        await_promise=True,
    )

    if isinstance(resultado, str):
        resultado = json.loads(resultado)

    if not isinstance(resultado, dict) or not resultado.get("ok"):
        motivo = (
            resultado.get("motivo", "Respuesta inesperada del navegador.")
            if isinstance(resultado, dict)
            else repr(resultado)
        )
        raise RuntimeError(
            f"No se pudo seleccionar el criterio Partida: {motivo}"
        )

    if not resultado.get("seleccionado"):
        raise RuntimeError(
            "El radio button Partida no quedó seleccionado."
        )

    await page.sleep(random.uniform(0.3, 0.6))

    print("[OK] Radio button 'Partida' seleccionado.")
    print(f"[Acción] Introduciendo número de partida: {numero!r}")

    # Establecer el valor mediante el setter nativo y emitir eventos
    # para que Angular reciba el cambio del campo.
    numero_json = json.dumps(numero)

    js_rellenar_numero = f"""
    (() => {{
        const input = document.querySelector(
            'input[formcontrolname="numero"]'
        );

        if (!input) {{
            return JSON.stringify({{
                ok: false,
                motivo: "No se encontró el campo numero."
            }});
        }}

        const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype,
            "value"
        ).set;

        input.focus();
        setter.call(input, "");
        input.dispatchEvent(new Event("input", {{ bubbles: true }}));

        setter.call(input, {numero_json});
        input.dispatchEvent(new Event("input", {{ bubbles: true }}));
        input.dispatchEvent(new Event("change", {{ bubbles: true }}));

        return JSON.stringify({{
            ok: true,
            valor: input.value
        }});
    }})()
    """

    resultado_input = await page.evaluate(
        js_rellenar_numero,
        await_promise=True,
    )

    if isinstance(resultado_input, str):
        resultado_input = json.loads(resultado_input)

    if (
        not isinstance(resultado_input, dict)
        or not resultado_input.get("ok")
    ):
        motivo = (
            resultado_input.get("motivo", "Respuesta inesperada.")
            if isinstance(resultado_input, dict)
            else repr(resultado_input)
        )
        raise RuntimeError(
            f"No se pudo rellenar el campo numero: {motivo}"
        )

    valor_real = str(resultado_input.get("valor", ""))

    if valor_real != numero:
        raise RuntimeError(
            "El campo numero no coincide con la partida solicitada. "
            f"Esperado={numero!r}; obtenido={valor_real!r}"
        )

    await page.sleep(random.uniform(0.3, 0.6))

    print(f"[OK] Campo numero verificado: {valor_real!r}")
