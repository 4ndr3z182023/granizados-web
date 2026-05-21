import os
import json
from datetime import datetime, date, timedelta
from functools import wraps
from time import time
from flask import Flask, render_template, jsonify, request, abort, Response
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
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            now = time()
            if ip not in request_counts:
                request_counts[ip] = []
            request_counts[ip] = [t for t in request_counts[ip] if now - t < window]
            if len(request_counts[ip]) >= max_requests:
                abort(429)
            request_counts[ip].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator

def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

def obtener_hora_colombia():
    """Retorna la fecha y hora actual en Colombia (UTC-5) libre de microsegundos."""
    utc_now = datetime.utcnow()
    colombia_now = utc_now - timedelta(hours=5)
    return colombia_now.replace(microsecond=0)

# --- RUTAS DEL DASHBOARD ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/get_data')
@rate_limit(max_requests=60, window=60)
def get_data():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return no_cache(jsonify([]))

        lista_ventas = [val for key, val in datos.items()]

        fecha_filtro = request.args.get('fecha')
        if fecha_filtro:
            try:
                fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
                lista_ventas = [
                    v for v in lista_ventas
                    if datetime.fromisoformat(v.get('timestamp', '').replace('Z', '')).date() == fecha_obj
                ]
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use YYYY-MM-DD"}), 400

        lista_ventas.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return no_cache(jsonify(lista_ventas))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/get_stats')
@rate_limit(max_requests=60, window=60)
def get_stats():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return no_cache(jsonify({"por_hora": {}, "total_hoy": 0, "ventas_hoy": 0}))

        fecha_filtro = request.args.get('fecha')
        if fecha_filtro:
            try:
                dia = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido. Use YYYY-MM-DD"}), 400
        else:
            dia = (datetime.utcnow() - timedelta(hours=5)).date()

        ventas_dia = [
            val for val in datos.values()
            if val.get('timestamp') and datetime.fromisoformat(val['timestamp'].replace('Z', '')).date() == dia
        ]

        por_hora = {}
        for v in ventas_dia:
            hora = datetime.fromisoformat(v['timestamp'].replace('Z', '')).hour
            por_hora[str(hora)] = por_hora.get(str(hora), 0) + 1

        total_dia = sum(v.get('valor_venta', 0) for v in ventas_dia)
        litros_consumidos = len(ventas_dia) * CONSUMO_POR_GRANIZADO

        return no_cache(jsonify({
            "por_hora": por_hora,
            "total_hoy": total_dia,
            "ventas_hoy": len(ventas_dia),
            "comision_total": round(total_dia * COMISION_PORCENTAJE),
            "litros_consumidos": litros_consumidos,
            "litros_restantes": max(0, CAPACIDAD_TANQUE - litros_consumidos),
            "porcentaje_meta": round((total_dia / META_DIARIA) * 100, 1)
        }))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update_data', methods=['POST', 'GET'])
@rate_limit(max_requests=120, window=60)
def update_data():
    try:
        precio = PRECIO_GRANIZADO
        metodo = "GeoVision Automatico"

        if request.method == 'POST' and request.is_json:
            body = request.get_json(silent=True) or {}
            precio = int(body.get('valor_venta', PRECIO_GRANIZADO))
            metodo = body.get('metodo', metodo)

        comision = round(precio * COMISION_PORCENTAJE)

        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": obtener_hora_colombia().isoformat(),
            "valor_venta": precio,
            "comision_empleado": comision,
            "metodo": metodo
        }
        ref.push(nueva_venta)
        
        return Response(
            json.dumps({"status": "ok", "venta": nueva_venta}, ensure_ascii=False),
            mimetype='application/json',
            status=200
        )

    except Exception as e:
        print(f"Error en recepción: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/export_csv')
@rate_limit(max_requests=10, window=60)
def export_csv():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()

        fecha_filtro = request.args.get('fecha')
        lista = list(datos.values()) if datos else []

        if fecha_filtro:
            try:
                fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
                lista = [
                    v for v in lista
                    if v.get('timestamp') and datetime.fromisoformat(v['timestamp'].replace('Z', '')).date() == fecha_obj
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


@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "Demasiadas solicitudes. Espera un momento."}), 429

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Error interno del servidor."}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=True, host='0.0.0.0', port=port)


