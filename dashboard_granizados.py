import os
import json
from datetime import datetime, date
from functools import wraps
from time import time
from flask import Flask, render_template, jsonify, request, abort
import firebase_admin
from firebase_admin import credentials, db

app = Flask(__name__)

# --- CONFIGURACIÓN DE SEGURIDAD PARA FIREBASE ---
firebase_json = os.environ.get('FIREBASE_JSON_DATA')

if firebase_json:
    key_dict = json.loads(firebase_json)
    cred = credentials.Certificate(key_dict)
else:
    try:
        cred = credentials.Certificate("llave.json")
    except Exception as e:
        print("Error: No se encontró llave.json ni variable de entorno.")
        cred = None

if cred:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://control-granizados-default-rtdb.firebaseio.com/'
        })
else:
    print("ALERTA: Firebase no se pudo inicializar.")

# --- CONSTANTES DE NEGOCIO ---
PRECIO_GRANIZADO = 5000
COMISION_PORCENTAJE = 0.10  # 10% de comisión al empleado
META_DIARIA = 103833
CAPACIDAD_TANQUE = 12.0  # litros
CONSUMO_POR_GRANIZADO = 0.25  # litros por granizado

# --- RATE LIMITING SIMPLE ---
request_counts = {}

def rate_limit(max_requests=30, window=60):
    """Limita peticiones por IP: max_requests por ventana de segundos."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            now = time()
            if ip not in request_counts:
                request_counts[ip] = []
            # Limpiar solicitudes viejas
            request_counts[ip] = [t for t in request_counts[ip] if now - t < window]
            if len(request_counts[ip]) >= max_requests:
                abort(429)
            request_counts[ip].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator

# --- RUTAS DEL DASHBOARD ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/get_data')
@rate_limit(max_requests=60, window=60)
def get_data():
    """Retorna todas las ventas, opcionalmente filtradas por fecha."""
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return jsonify([])

        lista_ventas = [val for key, val in datos.items()]

        # Filtro por fecha (parámetro opcional ?fecha=YYYY-MM-DD)
        fecha_filtro = request.args.get('fecha')
        if fecha_filtro:
            try:
                fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
                lista_ventas = [
                    v for v in lista_ventas
                    if datetime.fromisoformat(v.get('timestamp', '')).date() == fecha_obj
                ]
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use YYYY-MM-DD"}), 400

        lista_ventas.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return jsonify(lista_ventas)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/get_stats')
@rate_limit(max_requests=30, window=60)
def get_stats():
    """Retorna estadísticas agregadas: ventas por hora del día actual."""
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return jsonify({"por_hora": {}, "total_hoy": 0, "ventas_hoy": 0})

        hoy = date.today()
        ventas_hoy = [
            val for val in datos.values()
            if datetime.fromisoformat(val.get('timestamp', '')).date() == hoy
        ]

        por_hora = {}
        for v in ventas_hoy:
            hora = datetime.fromisoformat(v['timestamp']).hour
            por_hora[str(hora)] = por_hora.get(str(hora), 0) + 1

        total_hoy = sum(v.get('valor_venta', 0) for v in ventas_hoy)
        litros_consumidos = len(ventas_hoy) * CONSUMO_POR_GRANIZADO

        return jsonify({
            "por_hora": por_hora,
            "total_hoy": total_hoy,
            "ventas_hoy": len(ventas_hoy),
            "comision_total": round(total_hoy * COMISION_PORCENTAJE),
            "litros_consumidos": litros_consumidos,
            "litros_restantes": max(0, CAPACIDAD_TANQUE - litros_consumidos),
            "porcentaje_meta": round((total_hoy / META_DIARIA) * 100, 1)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update_data', methods=['POST', 'GET'])
@rate_limit(max_requests=120, window=60)
def update_data():
    """Registra una nueva venta (desde cámara GeoVision u otro origen)."""
    try:
        # Permitir precio personalizado si viene en el body
        precio = PRECIO_GRANIZADO
        metodo = "GeoVision Automático"

        if request.method == 'POST' and request.is_json:
            body = request.get_json(silent=True) or {}
            precio = int(body.get('valor_venta', PRECIO_GRANIZADO))
            metodo = body.get('metodo', metodo)

        comision = round(precio * COMISION_PORCENTAJE)

        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": datetime.now().isoformat(),
            "valor_venta": precio,
            "comision_empleado": comision,
            "metodo": metodo
        }
        ref.push(nueva_venta)
        return jsonify({"status": "ok", "venta": nueva_venta}), 200

    except Exception as e:
        print(f"Error en recepción: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/export_csv')
@rate_limit(max_requests=10, window=60)
def export_csv():
    """Exporta ventas a CSV descargable."""
    try:
        from flask import Response
        ref = db.reference('ventas_granizados')
        datos = ref.get()

        fecha_filtro = request.args.get('fecha')
        lista = list(datos.values()) if datos else []

        if fecha_filtro:
            try:
                fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
                lista = [
                    v for v in lista
                    if datetime.fromisoformat(v.get('timestamp', '')).date() == fecha_obj
                ]
            except ValueError:
                pass

        lista.sort(key=lambda x: x.get('timestamp', ''))

        lines = ["Timestamp,Valor Venta,Comision Empleado,Metodo"]
        for v in lista:
            lines.append(
                f"{v.get('timestamp','')},{v.get('valor_venta',0)},"
                f"{v.get('comision_empleado',0)},{v.get('metodo','')}"
            )

        csv_content = "\n".join(lines)
        filename = f"granizados_{fecha_filtro or date.today().isoformat()}.csv"
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Manejo de errores HTTP
@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "Demasiadas solicitudes. Espera un momento."}), 429

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Error interno del servidor."}), 500


# --- INICIO DEL SERVIDOR ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=True, host='0.0.0.0', port=port)

