import os
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request
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
    # Evitar errores si ya está inicializada
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://control-granizados-default-rtdb.firebaseio.com/'
        })
else:
    print("ALERTA: Firebase no se pudo inicializar.")

# --- RUTAS DEL DASHBOARD ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_data')
def get_data():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        if not datos:
            return jsonify([])
        
        lista_ventas = [val for key, val in datos.items()]
        return jsonify(lista_ventas[::-1])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ESTA ES LA RUTA QUE TE FALTABA PARA LA CÁMARA ---
@app.route('/update_data', methods=['POST', 'GET'])
def update_data():
    try:
        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": datetime.now().isoformat(),
            "valor_venta": 5000,
            "metodo": "GeoVision Automático"
        }
        ref.push(nueva_venta)
        return "OK - Venta Registrada", 200
    except Exception as e:
        print(f"Error en recepción: {e}")
        return f"Error: {e}", 500

# --- INICIO DEL SERVIDOR ---

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=True, host='0.0.0.0', port=port)
@app.route('/reset_data', methods=['POST'])
def reset_data():
    try:
        # Referencia a la tabla de ventas
        ref = db.reference('ventas_granizados')
        # Borramos todo el contenido
        ref.delete()
        return jsonify({"status": "success", "message": "Contador reiniciado"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
