import os
import json
import time
import cv2
import numpy as np
import threading
from datetime import datetime
import pytz
from flask import Flask, render_template, jsonify, request, Response
import firebase_admin
from firebase_admin import credentials, db

app = Flask(__name__)

# --- CONFIGURACIÓN DE PARÁMETROS DEL NEGOCIO ---
PRECIO_GRANIZADO = 5000
COMISION_PORCENTAJE = 0.10  # 10% de comisión para el empleado

# --- CONFIGURACIÓN DE LA CÁMARA GEOVISION ---
# Recuerda cambiar 'TU_CONTRASEÑA_REAL' por la clave de tu cámara
RTSP_URL = "rtsp://admin:TU_CONTRASEÑA_REAL@10.1.30.210:554/ch01/0"

# --- INITIALIZAR FIREBASE ---
try:
    cred = credentials.Certificate('serviceAccountKey.json')
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://granizados-web-default-rtdb.firebaseio.com/'
    })
    print("Conexión exitosa con Firebase Realtime Database.")
except Exception as e:
    print(f"Error al conectar con Firebase: {e}")

# --- FUNCIÓN DE HORA LOCAL COLOMBIA ---
def obtener_hora_colombia():
    zona_co = pytz.timezone('America/Bogota')
    return datetime.now(zona_co)

# --- PROCESO EN SEGUNDO PLANO: ANÁLISIS DE VIDEO (OpenCV Calibrado) ---
def monitorear_camara_granizados():
    print("Iniciando hilos y conexión con la cámara GeoVision...")
    cap = cv2.VideoCapture(RTSP_URL)
    
    # Subtractor de fondo para detectar objetos en movimiento
    fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    bloqueo_conteo = False 

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Pérdida de señal RTSP con la cámara. Reintentando en 10 segundos...")
            time.sleep(10)
            cap = cv2.VideoCapture(RTSP_URL)
            continue

        # Redimensionar el cuadro a 640x480 para procesar rápido en Render sin saturar memoria
        frame_pequeno = cv2.resize(frame, (640, 480))
        gray = cv2.cvtColor(frame_pequeno, cv2.COLOR_BGR2GRAY)
        fgmask = fgbg.apply(gray)
        
        # Umbralización para limpiar el ruido del movimiento
        _, thresh = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        movimiento_detectado = False
        for contour in contours:
            # Filtro por tamaño: Subimos a 1800 para ignorar cambios de luz del techo o moscas
            if cv2.contourArea(contour) < 1800: 
                continue
            
            (x, y, w, h) = cv2.boundingRect(contour)
            centro_x = x + int(w / 2)
            centro_y = y + int(h / 2)
            
            # --- CUADRANTE DE DETECCIÓN CALIBRADO ---
            # Evaluamos el movimiento únicamente en la zona inferior derecha del video (zona del mostrador)
            # Mitad derecha: centro_x > 320 | Parte baja: centro_y > 300
            if centro_x > 320 and centro_y > 300: 
                movimiento_detectado = True
                break

        # Disparar evento automático hacia Firebase
        if movimiento_detectado and not bloqueo_conteo:
            print("¡Objeto detectado en el mostrador! Registrando venta automática...")
            try:
                precio = PRECIO_GRANIZADO
                comision = round(precio * COMISION_PORCENTAJE)
                
                ref = db.reference('ventas_granizados')
                nueva_venta = {
                    "timestamp": obtener_hora_colombia().isoformat(),
                    "valor_venta": precio,
                    "comision_empleado": comision,
                    "metodo": "GeoVision Automatico"
                }
                ref.push(nueva_venta)
                print("¡Granizado sumado automáticamente a Firebase!")
            except Exception as e:
                print(f"Error registrando en Firebase: {e}")

            bloqueo_conteo = True 
            time.sleep(3) # Bloqueo de seguridad de 3 segundos para evitar doble conteo del mismo vaso

        if not movimiento_detectado:
            bloqueo_conteo = False 

        time.sleep(0.1) # Pausa de estabilidad

# Iniciar el análisis de la cámara en un hilo paralelo invisible
threading.Thread(target=monitorear_camara_granizados, daemon=True).start()


# --- RUTAS DE FLASK (SERVIDOR WEB) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_data', methods=['GET'])
def get_data():
    """Ruta interna para que el HTML consulte los datos desde Render sin bloqueos CORS"""
    try:
        ref = db.reference('ventas_granizados')
        ventas_data = ref.get() or {}
        
        lista_ventas = []
        for key, val in ventas_data.items():
            lista_ventas.append(val)
            
        return jsonify({"status": "success", "ventas": lista_ventas})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_data', methods=['POST', 'GET'])
def update_data():
    """Ruta de respaldo manual"""
    try:
        precio = PRECIO_GRANIZADO
        comision = round(precio * COMISION_PORCENTAJE)
        
        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": obtener_hora_colombia().isoformat(),
            "valor_venta": precio,
            "comision_empleado": comision,
            "metodo": "Registro Manual"
        }
        ref.push(nueva_venta)
        
        return Response(
            json.dumps({"status": "ok", "venta": nueva_venta}, ensure_ascii=False),
            mimetype='application/json',
            status=200
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

