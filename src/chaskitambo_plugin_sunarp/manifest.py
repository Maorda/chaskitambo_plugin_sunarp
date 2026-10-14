import json
import os
import threading
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ESTADOS_PARTIDA = {"pendiente", "procesando", "completada", "revision_manual", "error"}
ESTADOS_ASIENTO = {"pendiente", "descargando", "descargado", "error", "revision_manual"}

def fecha_actual() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def normalizar_texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()

class SunarpManifest:
    VERSION = 1

    def __init__(self, ruta: str | Path | None = None):
        if ruta is None:
            ruta = Path(__file__).resolve().parent / "estado_sunarp" / "manifest.json"
        self.ruta = Path(ruta).resolve()
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.ruta.exists():
            self._guardar({"version": self.VERSION, "actualizado_en": fecha_actual(), "partidas": {}})
        self._leer()

    @staticmethod
    def _clave_partida(oficina: str, area: str, partida: str, expediente: str = "") -> str:
        return json.dumps([normalizar_texto(oficina), normalizar_texto(area), normalizar_texto(partida), normalizar_texto(expediente)], ensure_ascii=False)

    def _leer(self) -> dict:
        with self._lock:
            if not self.ruta.exists():
                self._guardar({"version": self.VERSION, "actualizado_en": fecha_actual(), "partidas": {}})
            try:
                with self.ruta.open("r", encoding="utf-8") as archivo:
                    datos = json.load(archivo)
            except (json.JSONDecodeError, OSError) as exc:
                raise RuntimeError(f"No se pudo leer el manifiesto SUNARP '{self.ruta}': {exc}") from exc
            if not isinstance(datos, dict):
                raise RuntimeError("El manifiesto SUNARP no contiene un objeto JSON.")
            if not isinstance(datos.get("partidas"), dict):
                raise RuntimeError("El manifiesto SUNARP no contiene un diccionario 'partidas'.")
            if datos.get("version") != self.VERSION:
                raise RuntimeError(f"Versión de manifiesto incompatible: {datos.get('version')!r}. Versión admitida: {self.VERSION}.")
            return datos

    def _guardar(self, datos: dict) -> None:
        with self._lock:
            datos["actualizado_en"] = fecha_actual()
            temporal = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self.ruta.parent, prefix=f"{self.ruta.name}.", suffix=".tmp", delete=False) as archivo:
                    temporal = Path(archivo.name)
                    json.dump(datos, archivo, ensure_ascii=False, indent=2)
                    archivo.write("\n")
                    archivo.flush()
                    os.fsync(archivo.fileno())
                os.replace(temporal, self.ruta)
            except Exception:
                if temporal is not None:
                    try:
                        temporal.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

    def registrar_partida(self, oficina: str, area: str, partida: str, expediente: str = "") -> str:
        clave = self._clave_partida(oficina, area, partida, expediente)
        with self._lock:
            datos = self._leer()
            partidas = datos["partidas"]
            if clave not in partidas:
                ahora = fecha_actual()
                partidas[clave] = {
                    "oficina_registral": normalizar_texto(oficina),
                    "area_registral": normalizar_texto(area),
                    "partida_registral": normalizar_texto(partida),
                    "expediente": normalizar_texto(expediente) or None,
                    "estado": "pendiente",
                    "asientos": {},
                    "motivo": None,
                    "creado_en": ahora,
                    "actualizado_en": ahora,
                }
                self._guardar(datos)
        return clave

    def obtener_partida(self, clave: str) -> dict | None:
        with self._lock:
            datos = self._leer()
            registro = datos["partidas"].get(clave)
            if registro is None:
                return None
            return json.loads(json.dumps(registro, ensure_ascii=False))

    def iniciar_ejecucion_partida(self, clave: str, ejecucion_id: str, pid_proceso: int | None = None) -> None:
        with self._lock:
            datos = self._leer()
            registro = datos["partidas"].get(clave)
            if registro is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave}")
            estado = registro["estado"]
            if estado == "completada":
                raise RuntimeError("La partida ya figura como completada; no se iniciará otra ejecución.")
            if estado == "revision_manual":
                raise RuntimeError("La partida requiere revisión manual antes de reintentarse.")
            if estado == "procesando":
                raise RuntimeError("La partida ya figura como procesando. Comprueba si existe una ejecución activa antes de recuperarla.")
            registro["estado"] = "procesando"
            registro["ejecucion_id"] = ejecucion_id
            registro["pid_proceso"] = pid_proceso
            registro["inicio_procesamiento_en"] = fecha_actual()
            registro["heartbeat_en"] = fecha_actual()
            registro["motivo"] = None
            registro["actualizado_en"] = fecha_actual()
            self._guardar(datos)

    def registrar_heartbeat_partida(self, clave: str, ejecucion_id: str) -> None:
        with self._lock:
            datos = self._leer()
            registro = datos["partidas"].get(clave)
            if registro is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave}")
            if registro.get("ejecucion_id") != ejecucion_id:
                raise RuntimeError("El identificador de ejecución no coincide con el propietario registrado de la partida.")
            if registro["estado"] != "procesando":
                raise RuntimeError("No se puede registrar heartbeat: la partida no está procesándose.")
            registro["heartbeat_en"] = fecha_actual()
            registro["actualizado_en"] = fecha_actual()
            self._guardar(datos)

    def actualizar_partida(self, clave: str, estado: str, motivo: str | None = None) -> None:
        if estado not in ESTADOS_PARTIDA:
            raise ValueError(f"Estado de partida no válido: {estado}")
        with self._lock:
            datos = self._leer()
            registro = datos["partidas"].get(clave)
            if registro is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave}")
            registro["estado"] = estado
            registro["motivo"] = motivo
            registro["actualizado_en"] = fecha_actual()
            if estado != "procesando":
                registro["finalizacion_en"] = fecha_actual()
            self._guardar(datos)

    def registrar_asiento(self, clave_partida: str, asiento: str, descripcion: str = "") -> str:
        asiento = normalizar_texto(asiento)
        if not asiento:
            raise ValueError("El identificador del asiento no puede estar vacío.")
        with self._lock:
            datos = self._leer()
            partida = datos["partidas"].get(clave_partida)
            if partida is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave_partida}")
            if asiento not in partida["asientos"]:
                ahora = fecha_actual()
                partida["asientos"][asiento] = {
                    "asiento": asiento,
                    "descripcion": normalizar_texto(descripcion),
                    "estado": "pendiente",
                    "archivo": None,
                    "tamano_bytes": None,
                    "motivo": None,
                    "creado_en": ahora,
                    "actualizado_en": ahora,
                }
                self._guardar(datos)
        return asiento

    def actualizar_asiento(self, clave_partida: str, asiento: str, estado: str, archivo: str | None = None, tamano_bytes: int | None = None, motivo: str | None = None) -> None:
        if estado not in ESTADOS_ASIENTO:
            raise ValueError(f"Estado de asiento no válido: {estado}")
        if tamano_bytes is not None and tamano_bytes < 0:
            raise ValueError("tamano_bytes no puede ser negativo.")
        with self._lock:
            datos = self._leer()
            partida = datos["partidas"].get(clave_partida)
            if partida is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave_partida}")
            registro = partida["asientos"].get(normalizar_texto(asiento))
            if registro is None:
                raise KeyError(f"Asiento inexistente en el manifiesto: {asiento}")
            registro["estado"] = estado
            registro["archivo"] = archivo
            registro["tamano_bytes"] = tamano_bytes
            registro["motivo"] = motivo
            registro["actualizado_en"] = fecha_actual()
            self._guardar(datos)

    def obtener_partidas_pendientes(self) -> list[dict]:
        estados_pendientes = {"pendiente", "procesando", "error"}
        with self._lock:
            datos = self._leer()
            resultado = []
            for clave, registro in datos["partidas"].items():
                if registro.get("estado") in estados_pendientes:
                    copia = json.loads(json.dumps(registro, ensure_ascii=False))
                    copia["clave_manifest"] = clave
                    resultado.append(copia)
            return resultado

    def obtener_asientos_pendientes(self, clave_partida: str) -> list[dict]:
        with self._lock:
            datos = self._leer()
            partida = datos["partidas"].get(clave_partida)
            if partida is None:
                raise KeyError(f"Partida inexistente en el manifiesto: {clave_partida}")
            return [
                {"asiento": identificador, **json.loads(json.dumps(registro, ensure_ascii=False))}
                for identificador, registro in partida["asientos"].items()
                if registro.get("estado") != "descargado"
            ]

    def obtener_snapshot(self) -> dict:
        with self._lock:
            return self._leer()