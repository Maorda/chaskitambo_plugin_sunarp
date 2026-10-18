
# Archivo:
# D:\libs\chaskitambo_plugin_sunarp\src\chaskitambo_plugin_sunarp\acciones\__init__.py

# Exportaciones públicas del paquete acciones.

from .oficina import seleccionar_oficina
from .area import seleccionar_area
from .criterio import configurar_criterio_partida
from .buscar import (
    EstadoBusqueda,
    ResultadoBusqueda,
    ejecutar_busqueda_y_verificar,
)

# Compatibilidad con código anterior que importe el nombre antiguo.
ejecutar_busqueda_y_esperar_spinner = ejecutar_busqueda_y_verificar

__all__ = [
    "seleccionar_oficina",
    "seleccionar_area",
    "configurar_criterio_partida",
    "EstadoBusqueda",
    "ResultadoBusqueda",
    "ejecutar_busqueda_y_verificar",
    "ejecutar_busqueda_y_esperar_spinner",
]
