
# Archivo: acciones/extraer_bytes_pdf.py

import base64
import nodriver as uc


async def extraer_bytes_pdf(page: uc.Tab) -> bytes:
    """
    Recupera los bytes del PDF cargado en PDF.js.

    Debe ejecutarse en la pestaña donde ya se abrió el asiento.
    No inicia sesión ni realiza búsquedas por su cuenta.
    """

    js_script = """
    (async () => {
        const app = window.PDFViewerApplication;
        const doc = app?.pdfDocument;

        if (!doc || typeof doc.getData !== "function") {
            throw new Error(
                "PDFViewerApplication.pdfDocument no está disponible"
            );
        }

        const bytes = await doc.getData();

        if (!(bytes instanceof Uint8Array) || bytes.length === 0) {
            throw new Error("PDF.js devolvió bytes vacíos o inesperados");
        }

        // Transferencia binaria mediante Base64 en bloques.
        let binary = "";
        const chunkSize = 0x8000;

        for (let i = 0; i < bytes.length; i += chunkSize) {
            const chunk = bytes.subarray(
                i,
                Math.min(i + chunkSize, bytes.length)
            );
            binary += String.fromCharCode(...chunk);
        }

        return JSON.stringify({
            base64: btoa(binary),
            longitud: bytes.byteLength,
            paginas: doc.numPages
        });
    })()
    """

    try:
        resultado = await page.evaluate(
            js_script,
            await_promise=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Nodriver no pudo ejecutar la extracción PDF.js: {exc}"
        ) from exc

    # Según la versión de Nodriver, verifica que el resultado
    # sea el texto JSON esperado.
    if not isinstance(resultado, str):
        raise RuntimeError(
            "Nodriver no devolvió el texto JSON esperado. "
            f"Tipo recibido: {type(resultado).__name__}"
        )

    import json

    try:
        datos = json.loads(resultado)
        pdf_bytes = base64.b64decode(
            datos["base64"],
            validate=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo decodificar la respuesta de PDF.js: {exc}"
        ) from exc

    longitud_navegador = datos.get("longitud")

    if len(pdf_bytes) == 0:
        raise RuntimeError("La transferencia produjo cero bytes")

    if len(pdf_bytes) != longitud_navegador:
        raise RuntimeError(
            "La transferencia está incompleta: "
            f"navegador={longitud_navegador}, "
            f"Python={len(pdf_bytes)} bytes"
        )

    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError(
            "Los bytes recibidos no tienen la firma de un PDF"
        )

    if b"%%EOF" not in pdf_bytes[-2048:]:
        raise RuntimeError(
            "No se encontró el marcador final %%EOF "
            "en los últimos 2048 bytes"
        )

    print("[OK] Transferencia navegador → Python")
    print(f"[OK] Páginas: {datos.get('paginas')}")
    print(f"[OK] Bytes en navegador: {longitud_navegador:,}")
    print(f"[OK] Bytes en Python: {len(pdf_bytes):,}")
    print(f"[OK] Firma: {pdf_bytes[:5]!r}")
    print("[OK] Se encontró el marcador final %%EOF")

    return pdf_bytes
