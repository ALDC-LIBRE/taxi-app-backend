from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3
import math
from datetime import datetime
import urllib.request
import json

app = FastAPI()

# ==========================================
# 1. INICIALIZAR BASE DE DATOS SQLITE (Segura ante tablas existentes)
# ==========================================
def init_db():
    conn = sqlite3.connect("taxi.db")
    cursor = conn.cursor()
    
    # Tabla de usuarios
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            telefono TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol TEXT NOT NULL,
            tipo_vehiculo TEXT,
            verificado INTEGER DEFAULT 0,
            foto TEXT
        )
    """)
    
    # Asegurar que la columna foto exista si la tabla ya fue creada antes sin ella
    try:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN foto TEXT")
    except sqlite3.OperationalError:
        pass # La columna ya existe, continúa sin problemas
    
    # Tabla de configuración y tasas de cambio (Administrable desde la web)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor REAL NOT NULL
        )
    """)
    
    # Valores por defecto iniciales
    defaults = [
        ("tarifa_base", 150.0),
        ("precio_por_km", 50.0),
        ("tasa_usd", 320.0),  # Tasa de cambio USD a CUP configurable
        ("tasa_eur", 340.0)   # Tasa de cambio EUR a CUP configurable
    ]
    for clave, valor in defaults:
        cursor.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)", (clave, valor))

    # Tabla para el historial de viajes con estado por defecto 'solicitado'
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origen TEXT NOT NULL,
            destino TEXT NOT NULL,
            fecha TEXT NOT NULL,
            tarifa TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'solicitado'
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. MODELOS PYDANTIC
# ==========================================
class UsuarioCreate(BaseModel):
    nombre: str
    telefono: str
    password: str
    rol: str = "pasajero"
    tipo_vehiculo: Optional[str] = None

class LoginData(BaseModel):
    telefono: str
    password: str

class ViajeRequest(BaseModel):
    lat_origen: float
    lon_origen: float
    lat_destino: float
    lon_destino: float

class ViajeGuardar(BaseModel):
    origen: str
    destino: str
    tarifa: str

class ActualizarFoto(BaseModel):
    usuario_id: int
    foto: str

class TarifaUpdate(BaseModel):
    tarifa_base: float
    precio_por_km: float
    tasa_usd: float
    tasa_eur: float

# ==========================================
# 3. RUTAS DE AUTENTICACIÓN Y PERFIL
# ==========================================
@app.post("/register")
def register(usuario: UsuarioCreate):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        
        estado_verificado = 1 if usuario.rol == "pasajero" else 0
        
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, telefono, password, rol, tipo_vehiculo, verificado) 
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (usuario.nombre, usuario.telefono, usuario.password, usuario.rol, usuario.tipo_vehiculo, estado_verificado)
        )
        conn.commit()
        conn.close()
        
        mensaje = "Usuario registrado con éxito"
        if usuario.rol == "conductor":
            mensaje = "Registro exitoso. Tu cuenta de conductor está pendiente de validación."
            
        return {"mensaje": mensaje, "rol": usuario.rol}
        
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Este número de teléfono ya está registrado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/login")
def login(data: LoginData):
    conn = sqlite3.connect("taxi.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nombre, telefono, rol, verificado, foto FROM usuarios WHERE telefono = ? AND password = ?", 
        (data.telefono, data.password)
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="Teléfono o contraseña incorrectos.")

    user_id, nombre, telefono, rol, verificado, foto = user

    if rol == "conductor" and verificado == 0:
        raise HTTPException(
            status_code=403, 
            detail="Tu cuenta de conductor aún no ha sido verificada por el administrador."
        )

    return {
        "mensaje": "Login exitoso",
        "usuario": {
            "id": user_id,
            "nombre": nombre,
            "telefono": telefono,
            "rol": rol,
            "verificado": verificado,
            "foto": foto
        }
    }

@app.put("/actualizar-foto")
def actualizar_foto(data: ActualizarFoto):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET foto = ? WHERE id = ?", (data.foto, data.usuario_id))
        conn.commit()
        conn.close()
        return {"mensaje": "Foto actualizada con éxito"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 4. ENDPOINT PARA APROBAR CONDUCTORES
# ==========================================
@app.put("/aprobar-conductor/{conductor_id}")
def aprobar_conductor(conductor_id: int):
    conn = sqlite3.connect("taxi.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET verificado = 1 WHERE id = ? AND rol = 'conductor'", (conductor_id,))
    conn.commit()
    cambios = cursor.rowcount
    conn.close()

    if cambios == 0:
        raise HTTPException(status_code=404, detail="Conductor no encontrado o no es un conductor.")
    
    return {"mensaje": "Conductor verificado y aprobado exitosamente."}

# ==========================================
# 5. CONFIGURACIÓN DE TARIFAS Y TASAS (ADMIN)
# ==========================================
@app.get("/admin/tariffs")
def get_tariffs():
    conn = sqlite3.connect("taxi.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clave, valor FROM configuracion")
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

@app.put("/admin/tariffs")
def update_tariffs(data: TarifaUpdate):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE configuracion SET valor = ? WHERE clave = 'tarifa_base'", (data.tarifa_base,))
        cursor.execute("UPDATE configuracion SET valor = ? WHERE clave = 'precio_por_km'", (data.precio_por_km,))
        cursor.execute("UPDATE configuracion SET valor = ? WHERE clave = 'tasa_usd'", (data.tasa_usd,))
        cursor.execute("UPDATE configuracion SET valor = ? WHERE clave = 'tasa_eur'", (data.tasa_eur,))
        conn.commit()
        conn.close()
        return {"mensaje": "Tarifas y tasas actualizadas con éxito"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/streets/lookup")
def streets_lookup(lat: float, lon: float):
    try:
        # 1. Obtener nombre de la calle y municipio detallado con Nominatim
        url_nominatim = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18"
        req_nom = urllib.request.Request(url_nominatim, headers={'User-Agent': 'TaxiAppBackend'})
        
        calle_principal = "Ubicación"
        municipio = "La Habana"
        
        try:
            with urllib.request.urlopen(req_nom, timeout=2) as response:
                res = json.loads(response.read().decode())
                if "address" in res:
                    addr = res["address"]
                    calle_principal = addr.get("road") or addr.get("pedestrian") or "Ubicación"
                    municipio = (
                        addr.get("city_district") or 
                        addr.get("suburb") or 
                        addr.get("municipality") or 
                        addr.get("city") or 
                        "La Habana"
                    )
        except Exception:
            pass

        # 2. 🟢 CONSULTA QUIRÚRGICA: Obtener SOLO los nodos de la cuadra actual (radio de 45m)
        overpass_url = "http://overpass-api.de/api/interpreter"
        query = f"""
        [out:json][timeout:3];
        way(around:15,{lat},{lon})["highway"]->.main;
        node(w.main)(around:75,{lat},{lon})->.close_nodes;
        way(bn.close_nodes)["highway"]["name"]->.cross;
        (.cross; - .main;);
        out tags;
        """
        
        data_encoded = urllib.parse.urlencode({'data': query}).encode('utf-8')
        req_overpass = urllib.request.Request(overpass_url, data=data_encoded, headers={'User-Agent': 'TaxiAppBackend'})
        
        entrecalles = []
        try:
            with urllib.request.urlopen(req_overpass, timeout=3.5) as resp_op:
                resultado_op = json.loads(resp_op.read().decode())
                for el in resultado_op.get("elements", []):
                    nombre = el.get("tags", {}).get("name")
                    if nombre and nombre not in entrecalles:
                        entrecalles.append(nombre)
        except Exception as e:
            print(f"Error en Overpass: {e}")

        # 3. Construir el formato exacto: Calle C / Calle 13 y Calle 15
        if len(entrecalles) >= 2:
            direccion_exacta = f"{calle_principal} / {entrecalles[0]} y {entrecalles[1]}, {municipio}"
        elif len(entrecalles) == 1:
            direccion_exacta = f"{calle_principal} esq. {entrecalles[0]}, {municipio}"
        else:
            direccion_exacta = f"{calle_principal}, {municipio}"

        return {"direccion": direccion_exacta}

    except Exception as e:
        print(f"Error general en lookup: {e}")
        return {"direccion": f"Lat: {round(lat, 4)}, Lon: {round(lon, 4)}"}
    
# ==========================================
# 6. CÁLCULO DE KILÓMETROS Y TARIFA MULTIDIVISA (Haversine & OSRM)
# ==========================================
def calcular_distancia_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    return round(R * c, 2)

@app.post("/calcular-viaje")
def calcular_viaje(data: ViajeRequest):
    # Obtener configuración actual de la BD
    conn = sqlite3.connect("taxi.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clave, valor FROM configuracion")
    config = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    tarifa_base = config.get("tarifa_base", 150.0)
    precio_por_km = config.get("precio_por_km", 50.0)
    tasa_usd = config.get("tasa_usd", 320.0)
    tasa_eur = config.get("tasa_eur", 340.0)

    try:
        # URL de OSRM público (Calcula la ruta óptima para conducir)
        url = (
            f"http://router.project-osrm.org/route/v1/driving/"
            f"{data.lon_origen},{data.lat_origen};{data.lon_destino},{data.lat_destino}"
            f"?overview=full&geometries=geojson"
        )
        
        req = urllib.request.Request(url, headers={'User-Agent': 'TaxiApp'})
        with urllib.request.urlopen(req) as response:
            data_osrm = json.loads(response.read().decode())
            
        if data_osrm["code"] == "Ok":
            route = data_osrm["routes"][0]
            distancia_km = round(route["distance"] / 1000.0, 2)
            coordenadas_ruta = route["geometry"]["coordinates"]
            ruta_latlon = [{"latitude": coord[1], "longitude": coord[0]} for coord in coordenadas_ruta]
            
            costo_cup = tarifa_base + (distancia_km * precio_por_km)
            costo_usd = costo_cup / tasa_usd if tasa_usd > 0 else 0
            costo_eur = costo_cup / tasa_eur if tasa_eur > 0 else 0
            
            return {
                "distancia_km": distancia_km,
                "tarifa_total_cup": round(costo_cup, 2),
                "tarifa_total_usd": round(costo_usd, 2),
                "tarifa_total_eur": round(costo_eur, 2),
                "ruta_coordenadas": ruta_latlon
            }
        else:
            raise HTTPException(status_code=400, detail="No se pudo calcular la ruta.")
            
    except Exception as e:
        print(f"Error con OSRM (usando fallback lineal): {e}")
        # Fallback de emergencia a Haversine si OSRM no responde o falla internet externo
        distancia = calcular_distancia_haversine(data.lat_origen, data.lon_origen, data.lat_destino, data.lat_destino)
        costo_cup = tarifa_base + (distancia * precio_por_km)
        costo_usd = costo_cup / tasa_usd if tasa_usd > 0 else 0
        costo_eur = costo_cup / tasa_eur if tasa_eur > 0 else 0
        
        # Generar puntos intermedios para que la Polyline siempre se dibuje en el mapa
        ruta_fallback = []
        pasos = 15
        for i in range(pasos + 1):
            lat = data.lat_origen + (data.lat_destino - data.lat_origen) * (i / pasos)
            lon = data.lon_origen + (data.lon_destino - data.lon_origen) * (i / pasos)
            ruta_fallback.append({"latitude": lat, "longitude": lon})

        return {
            "distancia_km": distancia,
            "tarifa_total_cup": round(costo_cup, 2),
            "tarifa_total_usd": round(costo_usd, 2),
            "tarifa_total_eur": round(costo_eur, 2),
            "ruta_coordenadas": ruta_fallback
        }
# ==========================================
# 7. GESTIÓN DE VIAJES (Crear, Listar y Estados)
# ==========================================
@app.post("/crear-viaje")
def crear_viaje(data: ViajeGuardar):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        fecha_actual = datetime.now().strftime("%d %b, %H:%M")
        
        cursor.execute(
            """
            INSERT INTO viajes (origen, destino, fecha, tarifa, estado)
            VALUES (?, ?, ?, ?, 'solicitado')
            """,
            (data.origen, data.destino, fecha_actual, data.tarifa)
        )
        conn.commit()
        viaje_id = cursor.lastrowid
        conn.close()
        
        return {"mensaje": "Viaje solicitado con éxito", "id": viaje_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/historial-viajes")
def obtener_historial_viajes():
    try:
        conn = sqlite3.connect("taxi.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, origen, destino, fecha, tarifa, estado 
            FROM viajes 
            ORDER BY id DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        
        viajes_db = []
        for row in rows:
            viajes_db.append({
                "id": str(row["id"]),
                "origen": row["origen"],
                "destino": row["destino"],
                "fecha": row["fecha"],
                "tarifa": row["tarifa"],
                "estado": row["estado"]
            })
            
        return {"viajes": viajes_db}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/aceptar-viaje/{viaje_id}")
def aceptar_viaje(viaje_id: int):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE viajes SET estado = 'proceso' WHERE id = ?", (viaje_id,))
        conn.commit()
        conn.close()
        return {"mensaje": "Viaje aceptado por el conductor"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/iniciar-viaje/{viaje_id}")
def iniciar_viaje(viaje_id: int):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE viajes SET estado = 'viajando' WHERE id = ?", (viaje_id,))
        conn.commit()
        conn.close()
        return {"mensaje": "El viaje ha comenzado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/finalizar-viaje/{viaje_id}")
def finalizar_viaje(viaje_id: int):
    try:
        conn = sqlite3.connect("taxi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE viajes SET estado = 'completado' WHERE id = ?", (viaje_id,))
        conn.commit()
        conn.close()
        return {"mensaje": "Viaje completado exitosamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))