import os
import json
from flask import Flask, render_template, jsonify
import firebase_admin
from firebase_admin import credentials, db

app = Flask(__name__)

# --- CONFIGURACIÓN DE SEGURIDAD PARA FIREBASE (PC + NUBE) ---
# Intentamos leer la llave desde la variable de entorno (Render)
# Si no existe, buscamos el archivo local (Tu PC)
firebase_json = os.environ.get('FIREBASE_JSON_DATA')

if firebase_json:
    # Caso: Ejecución en la Nube (Render)
    key_dict = json.loads(firebase_json)
    cred = credentials.Certificate(key_dict)
else:
    # Caso: Ejecución Local (Tu computadora)
    # Asegúrate de que el archivo se llame exactamente llave.json
    try:
        cred = credentials.Certificate("llave.json")
    except Exception as e:
        print("Error: No se encontró llave.json ni variable de entorno.")
        cred = None

if cred:
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://control-granizados-default-rtdb.firebaseio.com/'
    })
else:
    print("ALERTA: Firebase no se pudo inicializar.")

# --- RUTAS DEL DASHBOARD ---

@app.route('/')
def index():
    # Carga tu archivo templates/index.html
    return render_template('index.html')

@app.route('/get_data')
def get_data():
    try:
        ref = db.reference('ventas_granizados')
        datos = ref.get()
        
        if not datos:
            return jsonify([])

        # Convertimos el diccionario de Firebase en una lista para el Dashboard
        lista_ventas = []
        for key, val in datos.items():
            lista_ventas.append(val)
        
        # Invertimos la lista para mostrar lo más reciente primero
        return jsonify(lista_ventas[::-1])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- INICIO DEL SERVIDOR ---

if __name__ == '__main__':
    # Usamos el puerto que asigne Render o el 8080 por defecto
    port = int(os.environ.get('PORT', 8080))
    # host='0.0.0.0' es vital para que sea visible desde la WAN
    app.run(debug=True, host='0.0.0.0', port=port)