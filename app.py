import os
import math
import random
import io
import json
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from supabase import create_client, Client  # type: ignore
import logging
import time

# Configurar el logger al inicio de tu app.py
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# 1. CONFIGURACIÓN DE SUPABASE
# ============================================================
# Si usas variables de entorno en Render, descomenta estas líneas:
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = None  # type: ignore
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase conectado")
    except Exception as e:  # type: ignore
        print(f"⚠️ Error conectando a Supabase: {e}")
else:
    print("⚠️ SUPABASE_URL o SUPABASE_KEY no configuradas en el entorno")
# ============================================================
# 2. INICIALIZACIÓN DE FLASK
# ============================================================
app = Flask(__name__)

# Habilitar CORS para toda la aplicación
# Esto permite que cualquier origen (como tu localhost) se comunique con la API
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": ["http://127.0.0.1:5500", "https://venezuelainsights.com"]
        }
    },
)  # Permite peticiones desde tu frontend (Netlify/Vercel)

# ============================================================
# 3. CONSTANTES DE RF (MISMA LÓGICA QUE EL FRONTEND)
# ============================================================
RF = {
    "TX_POWER": 20,  # dBm
    "FREQ": 2450,  # MHz
    "PATH_LOSS_EXP": 2.8,
    "WALL_LOSS": 5,
    "RSSI_THRESHOLD": -70,
    "GRID_STEP": 8,  # Para evaluación rápida en backend
}


# ============================================================
# 4. ALGORITMOS DE PROPAGACIÓN Y BRESENHAM (PYTHON)
# ============================================================
def bresenham_python(x0, y0, x1, y1):
    """Genera puntos en una línea recta entre dos puntos."""
    points = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    cx, cy = x0, y0
    while True:
        points.append((cx, cy))
        if cx == x1 and cy == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy
    return points


def point_to_segment_dist_py(px, py, x1, y1, x2, y2):
    vx = x2 - x1
    vy = y2 - y1
    dx = px - x1
    dy = py - y1
    dot = dx * vx + dy * vy
    len2 = vx * vx + vy * vy
    if len2 == 0:
        t = 0
    else:
        t = max(0, min(1, dot / len2))
    proj_x = x1 + t * vx
    proj_y = y1 + t * vy
    return math.hypot(px - proj_x, py - proj_y)


def calculate_rssi_py(tx_power, dist_m, wall_count):
    if dist_m < 0.1:
        return tx_power
    # Pérdida a 1 metro para 2.4 GHz
    pl1m = 40 + 20 * math.log10(RF["FREQ"] / 1000)
    path_loss = (
        pl1m
        + (10 * RF["PATH_LOSS_EXP"] * math.log10(dist_m))
        + (wall_count * RF["WALL_LOSS"])
    )
    return round(tx_power - path_loss, 2)


# ============================================================
# NUEVO: DETECTOR DE INTERFERENCIAS
# ============================================================
def diagnosticar_interferencias(respuestas):
    """
        respuestas: dict con las claves:
            - redes_vecinas: int (0, 1-3, 4-6, >6)
            - microondas: bool
            - bluetooth: bool
            - paredes_metal: bool
            - horas_pico: bool
            - soporta_5ghz: bool
            - dispositivos_conectados: int
        Retorna: dict con diagnóstico y recomendaciones.
    """
    diagnostico = {
        "nivel": "bajo",
        "mensaje": "",
        "recomendaciones": [],
        "canales_sugeridos": [],
        "acciones_urgentes": [],
    }

    # Factores de riesgo
    riesgo = 0
    factores = []

    # 1. Redes vecinas
    if respuestas.get("redes_vecinas", 0) > 6:
        riesgo += 3
        factores.append("Muchas redes vecinas (>6) saturan el espectro.")
        diagnostico["recomendaciones"].append(
            "Cambia a canales menos congestionados (1, 6 o 11) y usa 5 GHz si es posible."
        )
    elif respuestas.get("redes_vecinas", 0) > 3:
        riesgo += 2
        factores.append("Redes vecinas moderadas (4-6).")
        diagnostico["recomendaciones"].append(
            "Monitorea el canal más libre con un analizador de espectro."
        )

    # 2. Microondas
    if respuestas.get("microondas", False):
        riesgo += 2
        factores.append("Microondas cerca genera interferencia en 2.4 GHz.")
        diagnostico["recomendaciones"].append("Aleja el microondas del AP o usa 5 GHz.")

    # 3. Bluetooth
    if respuestas.get("bluetooth", False):
        riesgo += 1
        factores.append("Dispositivos Bluetooth pueden interferir en 2.4 GHz.")
        diagnostico["recomendaciones"].append("Usa canales 1, 6 o 11 y prioriza 5 GHz.")

    # 4. Paredes metálicas
    if respuestas.get("paredes_metal", False):
        riesgo += 2
        factores.append("Estructura metálica atenúa la señal y causa reflexiones.")
        diagnostico["recomendaciones"].append(
            "Coloca APs en línea de visión y usa antenas direccionales."
        )

    # 5. Horas pico
    if respuestas.get("horas_pico", False):
        riesgo += 1
        factores.append("El problema empeora en horas de mayor uso.")
        diagnostico["recomendaciones"].append(
            "Implementa QoS o balanceo de carga entre APs."
        )

    # 6. Dispositivos conectados
    dispositivos = respuestas.get("dispositivos_conectados", 0)
    if dispositivos > 20:
        riesgo += 2
        factores.append(f"Demasiados dispositivos ({dispositivos}) para un solo AP.")
        diagnostico["recomendaciones"].append(
            "Añade más APs o usa uno con mayor capacidad."
        )
    elif dispositivos > 10:
        riesgo += 1
        factores.append(f"{dispositivos} dispositivos conectados, cerca del límite.")
        diagnostico["recomendaciones"].append("Considera un AP adicional.")

    # 7. Soporte 5 GHz
    if not respuestas.get("soporta_5ghz", False):
        riesgo += 1
        factores.append("Equipo solo 2.4 GHz, más propenso a interferencias.")
        diagnostico["recomendaciones"].append(
            "Actualiza a APs con 5 GHz para menos congestión."
        )

    # Nivel de riesgo
    if riesgo >= 7:
        diagnostico["nivel"] = "crítico"
        diagnostico["mensaje"] = (
            "Interferencia severa detectada. Se recomienda una intervención inmediata."
        )
        diagnostico["acciones_urgentes"] = [
            "Realiza un site survey profesional con un analizador de espectro.",
            "Cambia de canal a 5 GHz (DFS si es posible).",
            "Reubica el AP principal lejos de fuentes de interferencia.",
        ]
    elif riesgo >= 4:
        diagnostico["nivel"] = "moderado"
        diagnostico["mensaje"] = "Interferencia significativa. Mejoras recomendadas."
        diagnostico["acciones_urgentes"] = [
            "Prueba canales menos congestionados (usa 1, 6 o 11).",
            "Si tienes 5 GHz, migra dispositivos críticos a esa banda.",
        ]
    else:
        diagnostico["nivel"] = "bajo"
        diagnostico["mensaje"] = "Interferencia baja. Tu red debería funcionar bien."
        diagnostico["acciones_urgentes"] = ["Mantén un monitoreo regular."]

    # Canales sugeridos (según nivel)
    if diagnostico["nivel"] in ["crítico", "moderado"]:
        diagnostico["canales_sugeridos"] = [
            "1",
            "6",
            "11 (para 2.4 GHz)",
            "36-48 (para 5 GHz)",
        ]
    else:
        diagnostico["canales_sugeridos"] = [
            "Cualquier canal libre (usa herramienta de escaneo)"
        ]

    return diagnostico


# ============================================================
# FUNCIONES GENERALES
# ============================================================
def guardar_lead_en_supabase(email, industry, product, source, metadata, template_used):
    """
    Guarda un lead en Supabase usando la función RPC 'insertar_lead'.
    Devuelve: (guardado_exitoso, mensaje_guardado)
    """
    guardado_exitoso = False
    mensaje_guardado = ""

    if email and supabase:
        try:
            result = supabase.rpc(
                "insertar_lead",
                {
                    "p_email": email,
                    "p_industry": industry,
                    "p_product": product,
                    "p_source": source,
                    "p_metadata": metadata,
                    "p_template_used": template_used,
                },
            ).execute()

            guardado_exitoso = True

            # Supabase puede devolver el resultado de la RPC
            # con un tipado genérico que Pylance no reconoce como dict.
            if result.data:
                lead_id = result.data[0]["id"]
            else:
                lead_id = "N/A"

            mensaje_guardado = f"Lead guardado con ID: {lead_id}"

            print(f"✅ {mensaje_guardado}")

        except Exception as e:
            mensaje_guardado = f"Error al guardar: {str(e)}"
            print(f"❌ {mensaje_guardado}")

    else:
        if not email:
            mensaje_guardado = "No se proporcionó email."
        else:
            mensaje_guardado = "Supabase no está conectado."

        print(f"⚠️ {mensaje_guardado}")

    return guardado_exitoso, mensaje_guardado

# Funciona, pero consumo muchos recursos y Render mata el proceso
def analizar_sombras_nada_mas():
    """
    Analiza las zonas de sombra (RSSI < -70 dBm) en el mapa de calor.
    Recibe: { aps, walls, width, height }
    Devuelve: { porcentaje_sombra, puntos_sombra, sugerencias }
    """
    try:
        start_time = time.time()
        logger.info("Iniciando análisis de sombras RF...")

        data = request.get_json()
        if not data:
            logger.warning("Petición rechazada: Faltan datos en el body.")
            return jsonify({"error": "Faltan datos"}), 400

        aps = data.get("aps", [])
        walls = data.get("walls", [])
        width = data.get("width", 800)
        height = data.get("height", 500)

        logger.info(f"Procesando {len(aps)} APs y {len(walls)} paredes...")

        if not aps:
            return jsonify({"error": "No hay puntos de acceso configurados"}), 400

        # ============================================================
        # Generar matriz de RSSI (usando las mismas funciones que el frontend)
        # ============================================================
        def count_walls_between(ax, ay, bx, by, walls):
            # (código completo - igual que en el frontend)
            if not walls:
                return 0
            # ... Bresenham ...
            return 0  # simplificado para el ejemplo, pero en el código real debes incluir la lógica completa

        # Para simplificar, aquí usamos la misma lógica que el frontend pero en Python.
        # Como ya tienes implementado el algoritmo en `app.py` (count_walls_between_py, etc.),
        # lo reutilizamos llamando a esas funciones.

        step = 6
        cols = int(width / step)
        rows = int(height / step)
        scale_x = 20 / width
        scale_y = 12.5 / height

        zonas_sombra = []
        total_puntos = rows * cols
        puntos_sombra = 0

        for r in range(rows):
            for c in range(cols):
                px = c * step + step / 2
                py = r * step + step / 2
                best_rssi = -100
                for ap in aps:
                    dx = (px - ap["x"]) * scale_x
                    dy = (py - ap["y"]) * scale_y
                    dist = math.hypot(dx, dy)
                    wall_count = count_walls_between_py(ap["x"], ap["y"], px, py, walls)
                    rssi = calculate_rssi_py(20, dist, wall_count)
                    if rssi > best_rssi:
                        best_rssi = rssi
                if best_rssi < -70:
                    puntos_sombra += 1
                    zonas_sombra.append({"x": px, "y": py, "rssi": best_rssi})

        porcentaje_sombra = (puntos_sombra / total_puntos) * 100

        # ============================================================
        # Generar sugerencias de ubicación de APs (zonas con mayor sombra)
        # ============================================================
        sugerencias = []
        if porcentaje_sombra > 20:
            # Agrupar zonas de sombra en clústeres (algoritmo simple: promedio)
            if zonas_sombra:
                # Centro de masa de todas las sombras
                centro_x = sum(p["x"] for p in zonas_sombra) / len(zonas_sombra)
                centro_y = sum(p["y"] for p in zonas_sombra) / len(zonas_sombra)
                sugerencias.append(
                    {
                        "x": round(centro_x, 1),
                        "y": round(centro_y, 1),
                        "justificacion": "Centro de la zona con mayor concentración de sombras",
                    }
                )
                # También sugerir un segundo AP en el punto más alejado
                if len(zonas_sombra) > 10:
                    # Punto más lejano al centro
                    lejano = max(
                        zonas_sombra,
                        key=lambda p: math.hypot(p["x"] - centro_x, p["y"] - centro_y),
                    )
                    sugerencias.append(
                        {
                            "x": round(lejano["x"], 1),
                            "y": round(lejano["y"], 1),
                            "justificacion": "Zona de sombra extrema",
                        }
                    )

        # ============================================================
        # Guardar lead (opcional, si el usuario lo solicita)
        # ============================================================
        email = data.get("email")
        if email:
            metadata = {
                "aps": aps,
                "porcentaje_sombra": porcentaje_sombra,
                "puntos_sombra": puntos_sombra,
                "sugerencias": sugerencias,
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=data.get("industry", "No especificado"),
                product="shadow_analyzer",
                source="web",
                metadata=metadata,
                template_used="shadow_analyzer",
            )
        else:
            guardado = False
            msg = "No se proporcionó email."

        elapsed_time = time.time() - start_time
        logger.info(
            f"Análisis completado en {elapsed_time:.2f} segundos. Puntos de sombra: {puntos_sombra}."
        )

        # ============================================================
        # Respuesta JSON
        # ============================================================
        return jsonify(
            {
                "porcentaje_sombra": round(porcentaje_sombra, 2),
                "puntos_sombra": puntos_sombra,
                "total_puntos": total_puntos,
                "sugerencias": sugerencias,
                "zonas_sombra": zonas_sombra[:200],  # limitar para no saturar
                "guardado": guardado,
                "mensaje_guardado": msg,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/analizar-sombras: {e}")
        logger.exception(f"❌ Error crítico en /api/analizar-sombras: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# FUNCIONES MATEMÁTICAS OPTIMIZADAS (Fuera del endpoint)
# ============================================================
def ccw(ax, ay, bx, by, cx, cy):
    """Determina si tres puntos están en orden en sentido antihorario."""
    return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)

def segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
    """Verifica si el segmento AB intersecta con el segmento CD."""
    return (ccw(ax, ay, cx, cy, dx, dy) != ccw(bx, by, cx, cy, dx, dy)) and (
        ccw(ax, ay, bx, by, cx, cy) != ccw(ax, ay, bx, by, dx, dy)
    )


def count_walls_between_py(ax, ay, px, py, walls):
    """Cuenta cuántas paredes cruza la línea de visión directa entre AP y Punto."""
    if not walls:
        return 0
    wall_hits = 0
    for w in walls:
        if segments_intersect(ax, ay, px, py, w["x1"], w["y1"], w["x2"], w["y2"]):
            wall_hits += 1
    return wall_hits


# ============================================================
# 5. EVALUADOR DE COBERTURA PARA OPTIMIZACIÓN
# ============================================================
def evaluate_coverage(ap_positions, width, height, walls):
    """Retorna porcentaje de cobertura (0-100) para un conjunto de APs."""
    step = RF["GRID_STEP"]
    cols = max(1, width // step)
    rows = max(1, height // step)
    covered = 0
    total = rows * cols

    # Escala: asumimos 20m x 12.5m (igual que frontend)
    scale_x = 20.0 / width
    scale_y = 12.5 / height

    for r in range(rows):
        for c in range(cols):
            px = c * step + step / 2
            py = r * step + step / 2
            real_x = px * scale_x
            real_y = py * scale_y

            best_rssi = -100
            for ap_x, ap_y in ap_positions:
                dx = (px - ap_x) * scale_x
                dy = (py - ap_y) * scale_y
                dist = math.hypot(dx, dy)
                wall_count = count_walls_between_py(ap_x, ap_y, px, py, walls)
                rssi = calculate_rssi_py(RF["TX_POWER"], dist, wall_count)
                if rssi > best_rssi:
                    best_rssi = rssi

            if best_rssi > RF["RSSI_THRESHOLD"]:
                covered += 1

    return (covered / total) * 100 if total > 0 else 0


# ============================================================
# 6. ALGORITMO DE OPTIMIZACIÓN (SIMULATED ANNEALING LIGERO)
# ============================================================
def optimize_aps(width, height, num_aps, walls, iterations=150):
    """Busca las mejores posiciones para los APs."""
    if num_aps <= 0:
        return []

    # Generar posiciones iniciales aleatorias (con margen)
    margin = 30
    best_positions = []
    for _ in range(num_aps):
        best_positions.append(
            (
                random.randint(margin, width - margin),
                random.randint(margin, height - margin),
            )
        )

    best_coverage = evaluate_coverage(best_positions, width, height, walls)

    # Simulated Annealing / Random Search
    for _ in range(iterations):
        # Hacer una copia y mutar ligeramente
        candidate = []
        for x, y in best_positions:
            # Movimiento aleatorio gaussiano
            new_x = x + random.gauss(0, width * 0.08)
            new_y = y + random.gauss(0, height * 0.08)
            new_x = max(margin, min(width - margin, new_x))
            new_y = max(margin, min(height - margin, new_y))
            candidate.append((new_x, new_y))

        cov = evaluate_coverage(candidate, width, height, walls)
        if cov > best_coverage:
            best_coverage = cov
            best_positions = candidate

    # Convertir a lista de diccionarios para JSON
    return [{"x": x, "y": y} for (x, y) in best_positions]


# ============================================================
# 7. ENDPOINT: /api/optimize
# ============================================================
@app.route("/api/optimize", methods=["POST"])
def optimize():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        width = data.get("width", 800)
        height = data.get("height", 500)
        ap_count = data.get("ap_count", 2)
        walls = data.get("walls", [])  # El frontend enviará las paredes

        # Validar límites
        if ap_count < 1:
            ap_count = 1
        if ap_count > 8:
            ap_count = 8

        optimized = optimize_aps(width, height, ap_count, walls)
        coverage = evaluate_coverage(
            [(p["x"], p["y"]) for p in optimized], width, height, walls
        )

        return jsonify({"aps": optimized, "coverage_percent": round(coverage, 2)})

    except Exception as e:
        print(f"Error en /api/optimize: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 8. ENDPOINT: /api/generate_report (CON PDF Y SUPABASE)
# ============================================================
@app.route("/api/generate_report", methods=["POST"])
def generate_report():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")
        aps = data.get("aps", [])
        template = data.get("template", "personalizado")
        coverage = data.get("coverage", "0%")
        avg_rssi = data.get("avgRssi", "0")

        # Validar email
        if not email or "@" not in email:
            return jsonify({"error": "Correo inválido"}), 400

        # ============================================================
        # 8.A GUARDAR EN SUPABASE
        # ============================================================
        if supabase:
            try:
                supabase.table("leads").insert(
                    {
                        "email": email,
                        "industry": industry,
                        "template_used": template,
                        "aps_count": len(aps),
                        "coverage": coverage.replace(
                            "%", ""
                        ),  # si viene con '%', lo limpiamos
                        "avg_rssi": avg_rssi,
                        "metadata": {"aps": aps, "version": "1.0"},
                        "product": "rf_optimizer",  # <- Añade esta línea si no está
                        "source": "web",  # <- o la fuente que quieras
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                ).execute()
                print(f"✅ Lead guardado: {email}")
            except Exception as e:
                print(f"⚠️ Error en Supabase: {e}")
                # No fallamos la petición por esto, el PDF sigue generándose.

        # ============================================================
        # 8.B GENERAR PDF PROFESIONAL
        # ============================================================
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        margin = 50
        y = height - margin

        # --- Título ---
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin, y, "Venezuela Insights - Informe de Optimización RF")
        y -= 30

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.grey)
        c.drawString(
            margin, y, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        y -= 25
        c.setFillColor(colors.black)

        # --- Datos del cliente ---
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Datos del Proyecto")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Correo: {email}")
        y -= 15
        c.drawString(margin, y, f"Industria / Giro: {industry}")
        y -= 15
        c.drawString(margin, y, f"Plantilla utilizada: {template.capitalize()}")
        y -= 25

        # --- Métricas principales ---
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Métricas de Cobertura")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(
            margin,
            y,
            f"📶 Cobertura estimada: {coverage} (mínimo aceptable: {RF['RSSI_THRESHOLD']} dBm)",
        )
        y -= 15
        c.drawString(margin, y, f"📊 RSSI Promedio: {avg_rssi} dBm")
        y -= 15
        c.drawString(margin, y, f"📡 Puntos de Acceso sugeridos: {len(aps)}")
        y -= 25

        # --- Posiciones de APs ---
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Ubicación Recomendada de APs")
        y -= 20
        c.setFont("Helvetica", 9)
        for idx, ap in enumerate(aps):
            c.drawString(
                margin,
                y,
                f"  AP {idx+1}: X={ap.get('x', 0):.1f}, Y={ap.get('y', 0):.1f} px",
            )
            y -= 15
        y -= 10

        # --- Recomendaciones ---
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Recomendaciones Técnicas")
        y -= 20
        c.setFont("Helvetica", 10)
        recomendaciones = [
            "• Utiliza canales 1, 6 y 11 para redes 2.4 GHz (evita solapamiento).",
            "• Para 5 GHz, prioriza canales DFS (52-144) si tu hardware lo soporta.",
            "• Asegura que los APs estén separados al menos 3 metros en zonas densas.",
            "• Si usas más de 3 APs, ajusta la potencia de transmisión para reducir interferencia.",
            "• Considera un estudio in-situ para validar estos resultados teóricos.",
        ]
        for rec in recomendaciones:
            c.drawString(margin, y, rec)
            y -= 18

        # --- Pie de página ---
        y = margin
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(colors.grey)
        c.drawString(
            margin,
            y,
            "Este informe es una simulación preliminar. Para resultados exactos, solicita un Site Survey profesional.",
        )
        c.drawRightString(width - margin, y, "v1.0 - Venezuela Insights")

        c.showPage()
        c.save()

        buffer.seek(0)

        # Retornar el PDF como archivo descargable
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"informe_rf_{datetime.now().strftime('%Y%m%d')}.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        print(f"Error en /api/generate_report: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 9. ENDPOINT DE SALUD (PARA RENDER)
# ============================================================
@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "supabase_connected": supabase is not None,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


@app.route("/api/diagnosticar-interferencias", methods=["POST"])
def diagnosticar():
    try:
        data = request.get_json()
        print(f"📩 Payload recibido: {json.dumps(data, indent=2)}")  # <-- LOG
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        # Extraer respuestas (con valores por defecto si no vienen)
        respuestas = {
            "redes_vecinas": int(data.get("redes_vecinas", 0)),
            "microondas": data.get("microondas", False),
            "bluetooth": data.get("bluetooth", False),
            "paredes_metal": data.get("paredes_metal", False),
            "horas_pico": data.get("horas_pico", False),
            "soporta_5ghz": data.get("soporta_5ghz", False),
            "dispositivos_conectados": int(data.get("dispositivos_conectados", 0)),
        }

        # Si el payload trae 'diagnostico' directamente, usarlo; si no, generarlo
        diagnostico = data.get("diagnostico")
        if not diagnostico:
            diagnostico = diagnosticar_interferencias(respuestas)

        # ============================================================
        # GUARDAR EN SUPABASE (solo si hay email)
        # ============================================================
        guardado_exitoso = False
        mensaje_guardado = ""
        email = data.get("email")

        print(f"📧 Email extraído: {email}")

        if email and supabase:
            try:
                metadata = {
                    "respuestas": respuestas,
                    "diagnostico": diagnostico,
                    "resumen": data.get("resumen", ""),
                }

                result = (
                    supabase.table("leads")
                    .insert(
                        {
                            "email": email,
                            "industry": data.get("industry", "No especificado"),
                            "product": "interference_detector",
                            "source": "web",
                            "metadata": metadata,
                            "template_used": "interference_detector",
                            "generated_at": datetime.utcnow().isoformat(),
                        }
                    )
                    .execute()
                )

                guardado_exitoso = True
                mensaje_guardado = f"Lead guardado con ID: {result.data[0]['id'] if result.data else 'N/A'}"
                print(f"✅ {mensaje_guardado}")

            except Exception as e:
                mensaje_guardado = f"Error al guardar: {str(e)}"
                print(f"❌ {mensaje_guardado}")
        else:
            if not email:
                mensaje_guardado = "No se proporcionó email."
            else:
                mensaje_guardado = "Supabase no está conectado."
            print(f"⚠️ {mensaje_guardado}")

        # ============================================================
        # RESPUESTA
        # ============================================================
        return jsonify(
            {
                "diagnostico": diagnostico,
                "resumen": f"Nivel de interferencia: {diagnostico['nivel'].upper()}",
                "guardado": guardado_exitoso,
                "mensaje_guardado": mensaje_guardado,
            }
        )

    except Exception as e:
        print(f"❌ Error general en /api/diagnosticar-interferencias: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 10. ENDPOINT: CALCULADORA DE CAPACIDAD
# ============================================================
@app.route("/api/calcular-capacidad", methods=["POST"])
def calcular_capacidad():
    """
    Calcula la capacidad necesaria de red WiFi basada en:
    - Número de usuarios concurrentes
    - Ancho de banda por usuario (Mbps)
    - Tipo de aplicación (streaming, navegación, mixto, etc.)
    Devuelve: APs necesarios, ancho de banda total, recomendaciones
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        # Parámetros de entrada
        usuarios = int(data.get("usuarios", 0))
        ancho_banda_por_usuario = float(data.get("ancho_banda", 5))
        tipo_aplicacion = data.get("tipo_aplicacion", "mixto")
        email = data.get("email")
        industry = data.get("industry", "No especificado")

        # Validaciones
        if usuarios <= 0:
            return jsonify({"error": "El número de usuarios debe ser mayor a 0"}), 400
        if ancho_banda_por_usuario <= 0:
            return jsonify({"error": "El ancho de banda debe ser mayor a 0"}), 400

        # ============================================================
        # CÁLCULOS
        # ============================================================

        # Factores de corrección según tipo de aplicación
        factores_concurrencia = {
            "streaming": 0.8,  # Alta demanda: muchos usuarios usan streaming
            "navegacion": 0.5,  # Baja demanda: navegación ligera
            "mixto": 0.65,  # Mixto: combinación promedio
            "video_llamadas": 0.75,  # Alta demanda: videollamadas constantes
            "iot": 0.3,  # Muy baja demanda: dispositivos IoT
        }
        factor_concurrencia = factores_concurrencia.get(tipo_aplicacion, 0.65)

        # Ancho de banda total requerido (Mbps)
        ancho_banda_total = usuarios * ancho_banda_por_usuario * factor_concurrencia

        # Capacidad real de un AP empresarial (Mbps) - incluye overhead
        capacidad_ap_mbps = 450  # AP Wi-Fi 5 (802.11ac) en condiciones reales

        # Si es Wi-Fi 6, mayor capacidad
        wifi_version = data.get("wifi_version", "wifi5")
        if wifi_version == "wifi6":
            capacidad_ap_mbps = 800  # Wi-Fi 6 (802.11ax) ofrece mayor throughput

        # Número de APs necesarios (redondeamos al alza)
        aps_necesarios = max(1, int(ancho_banda_total / capacidad_ap_mbps) + 1)

        # Capacidad por usuario (Mbps) después de repartir
        capacidad_por_usuario = round(ancho_banda_total / usuarios, 2)

        # ============================================================
        # RECOMENDACIONES
        # ============================================================
        recomendaciones = []

        # Recomendación básica de canales
        if aps_necesarios == 1:
            recomendaciones.append(
                "Un solo AP es suficiente. Usa canales 1, 6 o 11 para 2.4 GHz y canales 36-48 para 5 GHz."
            )
        elif aps_necesarios <= 3:
            recomendaciones.append(
                f"Distribuye los {aps_necesarios} APs en canales diferentes (1, 6, 11 para 2.4 GHz) para evitar interferencias."
            )
        else:
            recomendaciones.append(
                f"Con {aps_necesarios} APs, considera un controlador de red y uso de canales DFS para 5 GHz."
            )

        # Recomendación por tipo de aplicación
        if tipo_aplicacion == "streaming":
            recomendaciones.append(
                "Prioriza el uso de 5 GHz para streaming de video y evita la banda de 2.4 GHz."
            )
        elif tipo_aplicacion == "video_llamadas":
            recomendaciones.append(
                "Implementa QoS (Quality of Service) para priorizar tráfico de videollamadas."
            )
        elif tipo_aplicacion == "iot":
            recomendaciones.append(
                "Usa 2.4 GHz para dispositivos IoT y reserva 5 GHz para dispositivos críticos."
            )

        # Recomendación por densidad
        if usuarios > 50:
            recomendaciones.append(
                "Alta densidad de usuarios (>50). Considera APs con MU-MIMO y beamforming."
            )

        # ============================================================
        # GUARDAR EN SUPABASE (usando la función SECURITY DEFINER)
        # ============================================================
        guardado_exitoso = False
        mensaje_guardado = ""

        if email and supabase:
            try:
                # Usamos la función RPC que creamos anteriormente
                result = supabase.rpc(
                    "insertar_lead",
                    {
                        "p_email": email,
                        "p_industry": industry,
                        "p_product": "capacity_calculator",
                        "p_source": "web",
                        "p_metadata": {
                            "usuarios": usuarios,
                            "ancho_banda_por_usuario": ancho_banda_por_usuario,
                            "tipo_aplicacion": tipo_aplicacion,
                            "wifi_version": wifi_version,
                            "factor_concurrencia": factor_concurrencia,
                            "ancho_banda_total": ancho_banda_total,
                            "aps_necesarios": aps_necesarios,
                            "capacidad_por_usuario": capacidad_por_usuario,
                            "recomendaciones": recomendaciones,
                        },
                        "p_template_used": "capacity_calculator",
                    },
                ).execute()

                guardado_exitoso = True
                mensaje_guardado = f"Lead guardado con ID: {result.data['id'] if result.data else 'N/A'}"  # type: ignore
                print(f"✅ {mensaje_guardado}")

            except Exception as e:
                mensaje_guardado = f"Error al guardar: {str(e)}"
                print(f"❌ {mensaje_guardado}")
        else:
            if not email:
                mensaje_guardado = "No se proporcionó email."
            else:
                mensaje_guardado = "Supabase no está conectado."
            print(f"⚠️ {mensaje_guardado}")

        # ============================================================
        # RESPUESTA
        # ============================================================
        return jsonify(
            {
                "ancho_banda_total_mbps": round(ancho_banda_total, 2),
                "aps_necesarios": aps_necesarios,
                "capacidad_por_usuario": capacidad_por_usuario,
                "factor_concurrencia": factor_concurrencia,
                "recomendaciones": recomendaciones,
                "detalles": {
                    "usuarios": usuarios,
                    "ancho_banda_por_usuario": ancho_banda_por_usuario,
                    "tipo_aplicacion": tipo_aplicacion,
                    "wifi_version": wifi_version,
                },
                "guardado": guardado_exitoso,
                "mensaje_guardado": mensaje_guardado,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/calcular-capacidad: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 11. ENDPOINT: ANALIZADOR DE ZONAS DE SOMBRA
# ============================================================
@app.route("/api/analizar-sombras", methods=["POST"])
def analizar_sombras():
    """
    Analiza las zonas de sombra (RSSI < -70 dBm) en el mapa de calor.
    Recibe: { aps, walls, width, height, email, industry }
    Devuelve: { porcentaje_sombra, puntos_sombra, sugerencias }
    """
    try:
        start_time = time.time()
        logger.info("Iniciando análisis de sombras RF...")

        data = request.get_json()
        if not data:
            logger.warning("Petición rechazada: Faltan datos en el body.")
            return jsonify({'error': 'Faltan datos'}), 400

        aps = data.get('aps', [])
        walls = data.get('walls', [])
        width = data.get('width', 800)
        height = data.get('height', 500)

        logger.info(f"Procesando {len(aps)} APs y {len(walls)} paredes...")

        if not aps:
            return jsonify({'error': 'No hay puntos de acceso configurados'}), 400

        # ============================================================
        # Generar matriz de RSSI
        # ============================================================
        step = 6
        cols = int(width / step)
        rows = int(height / step)
        scale_x = 20 / width
        scale_y = 12.5 / height

        zonas_sombra = []
        total_puntos = rows * cols
        puntos_sombra = 0

        for r in range(rows):
            for c in range(cols):
                px = c * step + step / 2
                py = r * step + step / 2
                best_rssi = -100
                
                for ap in aps:
                    dx = (px - ap['x']) * scale_x
                    dy = (py - ap['y']) * scale_y
                    dist = math.hypot(dx, dy)
                    
                    # Llamada a la nueva función optimizada
                    wall_count = count_walls_between_py(ap['x'], ap['y'], px, py, walls)
                    
                    rssi = calculate_rssi_py(20, dist, wall_count)
                    if rssi > best_rssi:
                        best_rssi = rssi
                        
                if best_rssi < -70:
                    puntos_sombra += 1
                    zonas_sombra.append({'x': px, 'y': py, 'rssi': best_rssi})

        porcentaje_sombra = (puntos_sombra / total_puntos) * 100

        # ============================================================
        # Generar sugerencias de ubicación de APs
        # ============================================================
        sugerencias = []
        if porcentaje_sombra > 20:
            if zonas_sombra:
                centro_x = sum(p['x'] for p in zonas_sombra) / len(zonas_sombra)
                centro_y = sum(p['y'] for p in zonas_sombra) / len(zonas_sombra)
                sugerencias.append({
                    'x': round(centro_x, 1),
                    'y': round(centro_y, 1),
                    'justificacion': 'Centro de la zona con mayor concentración de sombras'
                })
                if len(zonas_sombra) > 10:
                    lejano = max(zonas_sombra, key=lambda p: math.hypot(p['x'] - centro_x, p['y'] - centro_y))
                    sugerencias.append({
                        'x': round(lejano['x'], 1),
                        'y': round(lejano['y'], 1),
                        'justificacion': 'Zona de sombra extrema'
                    })

        # ============================================================
        # Guardar lead
        # ============================================================
        email = data.get('email')
        if email:
            metadata = {
                'aps': aps,
                'porcentaje_sombra': porcentaje_sombra,
                'puntos_sombra': puntos_sombra,
                'sugerencias': sugerencias
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=data.get('industry', 'No especificado'),
                product='shadow_analyzer',
                source='web',
                metadata=metadata,
                template_used='shadow_analyzer'
            )
        else:
            guardado = False
            msg = "No se proporcionó email."

        elapsed_time = time.time() - start_time
        logger.info(f"Análisis completado en {elapsed_time:.2f} segundos. Puntos de sombra: {puntos_sombra}.")

        # ============================================================
        # Respuesta JSON
        # ============================================================
        return jsonify({
            'porcentaje_sombra': round(porcentaje_sombra, 2),
            'puntos_sombra': puntos_sombra,
            'total_puntos': total_puntos,
            'sugerencias': sugerencias,
            'zonas_sombra': zonas_sombra[:200],
            'guardado': guardado,
            'mensaje_guardado': msg
        })

    except Exception as e:
        logger.exception(f"❌ Error crítico en /api/analizar-sombras: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============================================================
# 12. PLANIFICADOR DE PRESUPUESTO
# ============================================================
@app.route("/api/generar-presupuesto", methods=["POST"])
def generar_presupuesto():
    """
    Calcula un presupuesto estimado para un estudio de RF basado en:
    - Metros cuadrados
    - Tipo de entorno
    - Número de APs actuales
    - Problemas específicos
    Devuelve: Desglose de costos, total, recomendaciones
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        # Parámetros de entrada
        metros_cuadrados = int(data.get("metros_cuadrados", 0))
        tipo_entorno = data.get("tipo_entorno", "oficina")
        aps_actuales = int(data.get("aps_actuales", 0))
        problemas = data.get("problemas", [])  # Lista de strings
        email = data.get("email")
        industry = data.get("industry", "No especificado")

        # Validaciones
        if metros_cuadrados <= 0:
            return jsonify({"error": "Los metros cuadrados deben ser mayor a 0"}), 400

        # ============================================================
        # CÁLCULO DE PRESUPUESTO
        # ============================================================

        # Tarifas base (ejemplo - ajusta según tu negocio)
        tarifas = {
            "site_survey_por_m2": 2.50,  # $/m²
            "analisis_espectro_fijo": 150.00,  # $ por sesión
            "informe_ejecutivo": 200.00,  # $ por informe
            "consultoria_por_hora": 85.00,  # $/hora
        }

        # Factores por tipo de entorno
        factores_entorno = {
            "oficina": 1.0,
            "centro_comercial": 1.4,
            "hospital": 1.6,
            "hotel": 1.3,
            "educacion": 1.2,
            "logistica": 1.3,
            "auditorio": 1.5,
            "otro": 1.2,
        }
        factor_entorno = factores_entorno.get(tipo_entorno, 1.0)

        # Factor por problemas específicos
        factor_problemas = 1.0
        if "cobertura" in problemas:
            factor_problemas += 0.1
        if "interferencia" in problemas:
            factor_problemas += 0.2
        if "capacidad" in problemas:
            factor_problemas += 0.15
        if "seguridad" in problemas:
            factor_problemas += 0.1

        # Cálculo de costos
        costo_site_survey = (
            metros_cuadrados * tarifas["site_survey_por_m2"] * factor_entorno
        )
        costo_analisis_espectro = tarifas["analisis_espectro_fijo"] * (
            1 + (0.2 if "interferencia" in problemas else 0)
        )
        costo_informe = tarifas["informe_ejecutivo"]
        costo_consultoria = tarifas["consultoria_por_hora"] * (
            2 if metros_cuadrados > 500 else 1
        )  # Más horas para espacios grandes

        # Subtotal
        subtotal = (
            costo_site_survey
            + costo_analisis_espectro
            + costo_informe
            + costo_consultoria
        )

        # Descuentos por volumen
        descuento = 0
        if metros_cuadrados > 1000:
            descuento = 0.10  # 10% descuento
        elif metros_cuadrados > 500:
            descuento = 0.05  # 5% descuento

        total = subtotal * (1 - descuento)

        # ============================================================
        # RECOMENDACIONES
        # ============================================================
        recomendaciones = []

        if metros_cuadrados > 500:
            recomendaciones.append(
                "Espacio grande (>500m²). Recomendamos un site survey detallado con múltiples APs."
            )
        if "interferencia" in problemas:
            recomendaciones.append(
                "Se detectó problema de interferencia. Incluye análisis de espectro con equipo profesional."
            )
        if "capacidad" in problemas:
            recomendaciones.append(
                "Problema de capacidad. Se recomienda estudio de densidad de usuarios y planificación de capacidad."
            )
        if aps_actuales == 0:
            recomendaciones.append(
                "No hay APs instalados. Se recomienda diseño completo de red desde cero."
            )

        if tipo_entorno in ["hospital", "centro_comercial"]:
            recomendaciones.append(
                f"Entorno {tipo_entorno.replace('_', ' ')} requiere planificación especial por normativas y alta densidad."
            )

        # ============================================================
        # GUARDAR LEAD (usando la función auxiliar)
        # ============================================================
        guardado_exitoso = False
        mensaje_guardado = ""

        if email:
            metadata = {
                "metros_cuadrados": metros_cuadrados,
                "tipo_entorno": tipo_entorno,
                "aps_actuales": aps_actuales,
                "problemas": problemas,
                "costo_site_survey": round(costo_site_survey, 2),
                "costo_analisis_espectro": round(costo_analisis_espectro, 2),
                "costo_informe": round(costo_informe, 2),
                "costo_consultoria": round(costo_consultoria, 2),
                "subtotal": round(subtotal, 2),
                "descuento": round(descuento * 100, 1),
                "total": round(total, 2),
                "recomendaciones": recomendaciones,
            }
            guardado_exitoso, mensaje_guardado = guardar_lead_en_supabase(
                email=email,
                industry=industry,
                product="budget_proposal",
                source="web",
                metadata=metadata,
                template_used="budget_proposal",
            )
        else:
            mensaje_guardado = "No se proporcionó email."

        # ============================================================
        # RESPUESTA
        # ============================================================
        return jsonify(
            {
                "presupuesto": {
                    "costo_site_survey": round(costo_site_survey, 2),
                    "costo_analisis_espectro": round(costo_analisis_espectro, 2),
                    "costo_informe": round(costo_informe, 2),
                    "costo_consultoria": round(costo_consultoria, 2),
                    "subtotal": round(subtotal, 2),
                    "descuento": round(descuento * 100, 1),
                    "total": round(total, 2),
                },
                "detalles": {
                    "metros_cuadrados": metros_cuadrados,
                    "tipo_entorno": tipo_entorno,
                    "aps_actuales": aps_actuales,
                    "problemas": problemas,
                },
                "recomendaciones": recomendaciones,
                "guardado": guardado_exitoso,
                "mensaje_guardado": mensaje_guardado,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/generar-presupuesto: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ARRANQUE
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
