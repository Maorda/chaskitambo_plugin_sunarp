MAPEO_CORTE_JUDICIAL_SUNARP = {
    # --- LIMA Y SEDES METROPOLITANAS ---
    "1828": "LIMA",  # Juzgados Civiles Subespecialidad Comercial (Tu caso actual)
    "1817": "LIMA",  # Sedes de Lima (Nuevo detectado)
    "1801": "LIMA",  # Lima Centro
    "1802": "LIMA",  # Lima Norte
    "1803": "LIMA",  # Lima Sur
    "1804": "LIMA",  # Lima Este
    "0701": "CALLAO",  # Callao
    "1303": "BARRANCA",  # Barranca
    "0801": "CAÑETE",  # Cañete
    "1301": "HUACHO",  # Huacho
    "1302": "HUARAL",  # Huaral

    # --- NORTE DEL PERÚ ---
    "1601": "TRUJILLO",  # Trujillo
    "2501": "CHIMBOTE",  # Chimbote
    "1701": "CHICLAYO",  # Chiclayo
    "2002": "PIURA",  # Piura
    "3101": "SULLANA",  # Sullana
    "2601": "TUMBES",  # Tumbes
    "0601": "CAJAMARCA",  # Cajamarca
    "1603": "CHEPEN",  # Chepén
    "0603": "CHOTA",  # Chota
    "1607": "HUAMACHUCO",  # Huamachuco
    "1604": "OTUZCO",  # Otuzco
    "1602": "SAN PEDRO",  # San Pedro
    "2502": "CASMA",  # Casma
    "3301": "JAEN",  # Jaén

    # --- CENTRO Y SUR DEL PERÚ ---
    "0401": "AREQUIPA",  # Arequipa
    "1001": "CUSCO",  # Cusco
    "1401": "ICA",  # Ica
    "1501": "HUANCAYO",  # Huancayo
    "0201": "HUARAZ",  # Huaraz
    "2901": "TACNA",  # Tacna
    "2101": "PUNO",  # Puno
    "2301": "MOQUEGUA",  # Moquegua
    "0501": "AYACUCHO",  # Ayacucho
    "1101": "HUANCAVELICA",  # Huancavelica
    "1201": "HUANUCO",  # Huánuco
    "0301": "ABANCAY",  # Abancay
    "0302": "ANDAHUAYLAS",  # Andahuaylas
    "0402": "CAMANA",  # Camaná
    "0404": "CASTILLA _ APLAO",  # Castilla - Aplao
    "1005": "ESPINAR",  # Espinar
    "0502": "HUANTA",  # Huanta
    "2302": "ILO",  # Ilo
    "0403": "ISLAY _ MOLLENDO",  # Islay - Mollendo
    "2102": "JULIACA",  # Juliaca
    "1506": "LA MERCED (SELVA CENTRAL)",  # La Merced
    "1403": "NAZCA",  # Nazca
    "2001": "PASCO",  # Pasco
    "1404": "PISCO",  # Pisco
    "1003": "QUILLABAMBA",  # Quillabamba
    "1507": "SATIPO",  # Satipo
    "1004": "SICUANI",  # Sicuani
    "1503": "TARMA",  # Tarma
    "1202": "TINGO MARIA",  # Tingo María

    # --- ORIENTE Y AMAZONÍA ---
    "1901": "MAYNAS",  # Maynas
    "2401": "PUCALLPA",  # Pucallpa
    "2201": "MOYOBAMBA",  # Moyobamba
    "0101": "CHACHAPOYAS",  # Chachapoyas
    "0102": "BAGUA",  # Bagua
    "2701": "MADRE DE DIOS",  # Madre de Dios
    "2204": "JUANJUI",  # Juanjuí
    "2202": "TARAPOTO",  # Tarapoto
    "2205": "YURIMAGUAS",  # Yurimaguas
    "1707": "LAMBAYEQUE",  # Lambayeque
    "0212": "ANCASH",  # Huaraz
}   

def obtener_nombre_oficina(expediente: str) -> str | None:
    """Extrae el código de corte y valida contra el maestro. Retorna None si no existe."""
    if not expediente or not isinstance(expediente, str):
        return None
    
    partes = expediente.split("-")
    if len(partes) >= 4:
        codigo_corte = partes[3]  # Ej: '1828'
        return MAPEO_CORTE_JUDICIAL_SUNARP.get(codigo_corte, None)
        
    return None