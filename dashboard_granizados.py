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
from firebase_admin import credentials, db, storage

app = Flask(__name__)

# --- CONFIGURACIÓN DE PARÁMETROS DEL NEGOCIO ---
PRECIO_GRANIZADO = 5000
COMISION_PORCENTAJE = 0.10  # 10% de comisión para el empleado

# --- CONFIGURACIÓN DE LA CÁMARA GEOVISION ---
RTSP_URL = "rtsp://admin:4lph48390+@216.24.57.251:554/ch01/0"

# --- INITIALIZAR FIREBASE ---
try:
    cred = credentials.Certificate('serviceAccountKey.json')
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://granizados-web-default-rtdb.firebaseio.com/',
        # REEMPLAZA AQUÍ EL BUCKET DE STORAGE DE TU CONSOLA (Sin el gs://)
        'storageBucket': 'granizados-web.appspot.com' 
    })
    print("Conexión exitosa con Firebase (Database y Storage).")
except Exception as e:
    print(f"Error al conectar con Firebase: {e}")

# --- FUNCIÓN DE HORA LOCAL COLOMBIA ---
def obtener_hora_colombia():
    zona_co = pytz.timezone('America/Bogota')
    return datetime.now(zona_co)

# --- PROCESO EN SEGUNDO PLANO: ANÁLISIS DE VIDEO Y CAPTURA DE FOTOS ---
def monitorear_camara_granizados():
    print("Iniciando hilos de la cámara GeoVision...")
    cap = cv2.VideoCapture(RTSP_URL)
    
    # Subtractor de fondo para detectar el movimiento de objetos
    fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    bloqueo_conteo = False 

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Pérdida de señal RTSP con la cámara. Reintentando en 10 segundos...")
            time.sleep(10)
            cap = cv2.VideoCapture(RTSP_URL)
            continue

        # Redimensionar el cuadro a 640x480 para procesar rápido en Render sin saturar la CPU
        frame_pequeno = cv2.resize(frame, (640, 480))
        gray = cv2.cvtColor(frame_pequeno, cv2.COLOR_BGR2GRAY)
        fgmask = fgbg.apply(gray)
        
        # Limpieza de ruido en el movimiento detectado
        _, thresh = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        movimiento_detectado = False
        for contour in contours:
            # Filtro por tamaño: Ignora cambios de luz menores o pequeños reflejos
            if cv2.contourArea(contour) < 1800: 
                continue
            
            (x, y, w, h) = cv2.boundingRect(contour)
            centro_x = x + int(w / 2)
            centro_y = y + int(h / 2)
            
            # --- CUADRANTE DE DETECCIÓN CALIBRADO ---
            # Evaluamos el movimiento únicamente en el mostrador (zona inferior derecha)
            # Mitad derecha: centro_x > 320 | Parte baja: centro_y > 300
            if centro_x > 320 and centro_y > 300: 
                movimiento_detectado = True
                break

        # Disparar conteo automático y capturar fotografía de evidencia
        if movimiento_detectado and not bloqueo_conteo:
            print("¡Objeto detectado en el mostrador! Capturando evidencia visual...")
            
            # 1. Guardar la foto localmente de forma temporal en el servidor
            nombre_foto = f"evidencia_{int(time.time())}.jpg"
            cv2.imwrite(nombre_foto, frame_pequeno)
            
            url_foto_publica = ""
            try:
                # 2. Subir la imagen guardada al Firebase Storage de tu proyecto
                bucket = storage.bucket()
                blob = bucket.blob(f"evidencias_despacho/{nombre_foto}")
                blob.upload_from_filename(nombre_foto)
                
                # Hacer el archivo accesible públicamente para que el Dashboard lo renderice
                blob.make_public()
                url_foto_publica = blob.public_url
                
                # Limpieza inmediata: Borramos la foto local para mantener libre el disco de Render
                if os.path.exists(nombre_foto):
                    os.remove(nombre_foto)
            except Exception as e:
                print(f"Error subiendo fotografía a Storage: {e}")

            # 3. Guardar el registro de la venta en Realtime Database vinculando la foto
            try:
                precio = PRECIO_GRANIZADO
                comision = round(precio * COMISION_PORCENTAJE)
                
                ref = db.reference('ventas_granizados')
                nueva_venta = {
                    "timestamp": obtener_hora_colombia().isoformat(),
                    "valor_venta": precio,
                    "comision_empleado": comision,
                    "metodo": "GeoVision Automatico",
                    "foto_url": url_foto_publica  # Guardamos la URL pública de la foto
                }
                ref.push(nueva_venta)
                print("¡Venta y fotografía registradas exitosamente en Firebase!")
            except Exception as e:
                print(f"Error registrando base de datos: {e}")

            bloqueo_conteo = True 
            time.sleep(3) # Bloqueo de seguridad para evitar dobles conteos del mismo vaso

        if not movimiento_detectado:
            bloqueo_conteo = False 

        time.sleep(0.1)

# Iniciar el proceso de análisis de video de forma asíncrona en paralelo
threading.Thread(target=monitorear_camara_granizados, daemon=True).start()


# --- RUTAS DE FLASK (SERVIDOR WEB) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_data', methods=['GET'])
def get_data():
    """Endpoint interno para transferir la data desde Firebase hacia tu navegador sin CORS"""
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
    """Ruta opcional para registrar ventas manuales con un clic"""
    try:
        precio = PRECIO_GRANIZADO
        comision = round(precio * COMISION_PORCENTAJE)
        
        ref = db.reference('ventas_granizados')
        nueva_venta = {
            "timestamp": obtener_hora_colombia().isoformat(),
            "valor_venta": precio,
            "comision_empleado": comision,
            "metodo": "Registro Manual",
            "foto_url": ""  # Los registros manuales no contienen imagen
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

