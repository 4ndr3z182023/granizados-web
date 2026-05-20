import os
import json
from datetime import datetime, date, timedelta
from functools import wraps
from time import time
from flask import Flask, render_template, jsonify, request, abort, session
import firebase_admin
from firebase_admin import credentials, db
import hashlib
import hmac

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tu-clave-secreta-cambiala-en-produccion')

# --- CONFIGURACIÓN DE SEGURIDAD ---
PASSWORD_ADMIN = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- CONFIGURACIÓN DE FIREBASE ---
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
        print("✅ Firebase inicializado correctamente")
else:
    print("❌ ALERTA: Firebase no se pudo inicializar.")

# --- CONSTANTES DE NEGOCIO ---
PRECIO_GRANIZADO = 5000
COMISION_PORCENTAJE = 0.10  # 10%
META_DIARIA = 103833
CAPACIDAD_TANQUE = 12.0  # litros
CONSUMO_POR_GRANIZADO = 0.25  # litros por granizado
COSTO_POR_GRANIZADO = 1800  # costo de insumos

# --- RATE LIMITING ---
request_counts = {}

def rate_limit(max_requests=60, window=60):
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

# --- DECORADOR PARA REQUERIR AUTENTICACIÓN ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"error": "No autorizado"}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- RUTAS ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    if data.get('password') == PASSWORD_ADMIN:
        session['logged_in'] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    return jsonify({"success": True})

def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

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
                    if datetime.fromisoformat(v.get('timestamp', '')).date() == fecha_obj
                ]
            except ValueError:
                return jsonify({"error": "Formato de fecha inválido"}), 400

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
            return no_cache(jsonify({
                "por_hora": {}, "total_hoy": 0, "ventas_hoy": 0,
                "comision_total": 0, "litros_consumidos": 0, "litros_restantes": CAPACIDAD_TANQUE,
                "porcentaje_meta": 0, "ganancia_neta": 0, "costo_total": 0
            }))

        fecha_filtro = request.args.get('fecha')
        if fecha_filtro:
            try:
                dia = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({"error": "Formato inválido"}), 400
        else:
            dia = date.today()

        ventas_dia = [
            val for val in datos.values()
            if datetime.fromisoformat(val.get('timestamp', '')).date() == dia
        ]

        por_hora = {}
        for v in ventas_dia:
            hora = datetime.fromisoformat(v['timestamp']).hour
            por_hora[str(hora)] = por_hora.get(str(hora), 0) + 1

        total_dia = sum(v.get('valor_venta', 0) for v in ventas_dia)
        litros_consumidos = len(ventas_dia) * CONSUMO_POR_GRANIZADO
        ganancia_neta = total_dia - (len(ventas_dia) * COSTO_POR_GRANIZADO)

        return no_cache(jsonify({
            "por_hora": por_hora,
            "total_hoy": total_dia,
            "ventas_hoy": len(ventas_dia),
            "comision_total": round(total_dia * COMISION_PORCENTAJE),
            "litros_consumidos": round(litros_consumidos, 2),
            "litros_restantes": round(max(0, CAPACIDAD_TANQUE - litros_consumidos), 2),
            "porcentaje_meta": round((total_dia / META_DIARIA) * 100, 1),
            "ganancia_neta": round(ganancia_neta),
            "costo_total": len(ventas_dia) * COSTO_POR_GRANIZADO
        }))

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_weekly_summary')
@rate_limit(max_requests=30, window=60)
def get_weekly_summary():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return jsonify([])

        resumen_semanal = []
        for i in range(7):
            fecha = date.today() - timedelta(days=i)
            ventas_dia = [
                v for v in datos.values()
                if datetime.fromisoformat(v.get('timestamp', '')).date() == fecha
            ]
            total_dia = sum(v.get('valor_venta', 0) for v in ventas_dia)
            resumen_semanal.append({
                "fecha": fecha.isoformat(),
                "total": total_dia,
                "ventas": len(ventas_dia),
                "dia_semana": fecha.strftime('%A')
            })
        
        return jsonify(resumen_semanal[::-1])  # Orden cronológico
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/update_data', methods=['POST'])
@rate_limit(max_requests=120, window=60)
def update_data():
    """Recibe ventas desde la cámara GeoVision o desde el botón manual"""
    try:
        data = request.get_json(silent=True) or {}
        precio = int(data.get('valor_venta', PRECIO_GRANIZADO))
        metodo = data.get('metodo', 'GeoVision Automático')
        observacion = data.get('observacion', '')

        comision = round(precio * COMISION_PORCENTAJE)

        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": datetime.now().isoformat(),
            "valor_venta": precio,
            "comision_empleado": comision,
            "metodo": metodo,
            "observacion": observacion
        }
        ref.push(nueva_venta)
        
        print(f"✅ Venta registrada: {metodo} - ${precio} - {datetime.now().strftime('%H:%M:%S')}")
        return jsonify({"status": "ok", "venta": nueva_venta}), 200

    except Exception as e:
        print(f"❌ Error en recepción: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/export_csv')
@rate_limit(max_requests=10, window=60)
def export_csv():
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

        lines = ["Timestamp,Valor Venta,Comision Empleado,Metodo,Observacion"]
        for v in lista:
            lines.append(
                f"{v.get('timestamp','')},{v.get('valor_venta',0)},"
                f"{v.get('comision_empleado',0)},{v.get('metodo','')},"
                f"{v.get('observacion','')}"
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
    app.run(debug=False, host='0.0.0.0', port=port)  # debug=False para producción
