# Este archivo convierte la carpeta 'acciones' en un paquete modular de Python.
# Expone las funciones principales directamente para limpiar las importaciones del orquestador.

from .oficina import seleccionar_oficina
from .area import seleccionar_area
from .criterio import configurar_criterio_partida
from .buscar import ejecutar_busqueda_y_esperar_spinner
from .verificador import verificar_estado_y_previsualizar

# Definimos los módulos autorizados para exportación masiva
__all__ = [
    "seleccionar_oficina",
    "seleccionar_area",
    "configurar_criterio_partida",
    "ejecutar_busqueda_y_esperar_spinner",
    "verificar_estado_y_previsualizar"
]
