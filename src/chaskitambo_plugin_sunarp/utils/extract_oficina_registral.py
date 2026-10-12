import re

def extraer_codigo_oficina(expediente: str) -> str:
    """
    Extrae el código de oficina/sede registral o distrito judicial (ej: '2501')
    a partir de un número de expediente judicial peruano estándar.
    
    Formato esperado: NNNNN-AAAA-I-DDDD-XX-YY-ZZ
    Ejemplo: '00131-2020-0-2501-JP-CI-03' -> '2501'
    """
    if not expediente:
        return "N/A"
        
    # Limpiamos espacios en blanco por si acaso
    expediente = expediente.strip()
    
    # Opción 1: Usando Expresión Regular (Más seguro por si el formato varía ligeramente)
    patron = r'^\d{5}-\d{4}-\d-(\d{4})-'
    match = re.match(patron, expediente)
    
    if match:
        return match.group(1)
        
    # Opción 2 (Fallback): División directa por guiones
    partes = expediente.split('-')
    if len(partes) >= 4:
        return partes[3]
        
    return "N/A"

# --- Prueba rápida ---
if __name__ == "__main__":
    exp = "00131-2020-0-2501-JP-CI-03"
    codigo = extraer_codigo_oficina(exp)
    print(f"Expediente : {exp}")
    print(f"Código     : {codigo}")