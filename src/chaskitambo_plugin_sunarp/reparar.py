import os

# Ruta al archivo problemático dentro de tu venv
ruta_archivo = r"C:\Users\Manrique Guzman\Desktop\instalador\sunarp_raspador\venv\Lib\site-packages\nodriver\cdp\network.py"

if os.path.exists(ruta_archivo):
    # Leer el archivo ignorando errores de codificación
    with open(ruta_archivo, 'r', encoding='utf-8', errors='ignore') as f:
        lineas = f.readlines()
    
    # Modificar la línea conflictiva (línea 1345, índice 1344)
    if len(lineas) >= 1345:
        lineas[1344] = "    #: JSON.\n" # Reemplaza el comentario roto por uno limpio
        print("[OK] Línea 1345 corregida localmente.")
    
    # Guardar el archivo forzando codificación UTF-8 pura
    with open(ruta_archivo, 'w', encoding='utf-8') as f:
        f.writelines(lineas)
    print("[ÉXITO] Archivo network.py reparado correctamente.")
else:
    print("[ERROR] No se encontró el archivo en la ruta especificada.")
