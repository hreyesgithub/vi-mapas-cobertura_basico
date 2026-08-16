import os
import math
import random
import io
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from supabase import create_client, Client # type: ignore

# ============================================================
# 1. CONFIGURACIÓN DE SUPABASE
# ============================================================
# Si usas variables de entorno en Render, descomenta estas líneas:
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
# Si no, ponlas directamente aquí (NO recomendado para producción, pero válido para pruebas rápidas):
#SUPABASE_URL = "https://wicezigksaezaroixuyc.supabase.co"  # <--- CAMBIAME
#SUPABASE_KEY = "tu-anon-key-publica"              # <--- CAMBIAME

supabase: Client = None # type: ignore
if SUPABASE_URL and SUPABASE_KEY and SUPABASE_URL != "https://wicezigksaezaroixuyc.supabase.co":
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# 2. INICIALIZACIÓN DE FLASK
# ============================================================
app = Flask(__name__)
CORS(app)  # Permite peticiones desde tu frontend (Netlify/Vercel)

# ============================================================
# 3. CONSTANTES DE RF (MISMA LÓGICA QUE EL FRONTEND)
# ============================================================
RF = {
    'TX_POWER': 20,          # dBm
    'FREQ': 2450,            # MHz
    'PATH_LOSS_EXP': 2.8,
    'WALL_LOSS': 5,
    'RSSI_THRESHOLD': -70,
    'GRID_STEP': 8,          # Para evaluación rápida en backend
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

def count_walls_between_py(ax, ay, bx, by, walls):
    if not walls:
        return 0
    line_points = bresenham_python(int(ax), int(ay), int(bx), int(by))
    hits = 0
    for (px, py) in line_points:
        for w in walls:
            d = point_to_segment_dist_py(px, py, w['x1'], w['y1'], w['x2'], w['y2'])
            if d < 4.0:
                hits += 1
                break
    return hits // 6

def calculate_rssi_py(tx_power, dist_m, wall_count):
    if dist_m < 0.1:
        return tx_power
    # Pérdida a 1 metro para 2.4 GHz
    pl1m = 40 + 20 * math.log10(RF['FREQ'] / 1000)
    path_loss = pl1m + (10 * RF['PATH_LOSS_EXP'] * math.log10(dist_m)) + (wall_count * RF['WALL_LOSS'])
    return round(tx_power - path_loss, 2)

# ============================================================
# 5. EVALUADOR DE COBERTURA PARA OPTIMIZACIÓN
# ============================================================
def evaluate_coverage(ap_positions, width, height, walls):
    """Retorna porcentaje de cobertura (0-100) para un conjunto de APs."""
    step = RF['GRID_STEP']
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
            for (ap_x, ap_y) in ap_positions:
                dx = (px - ap_x) * scale_x
                dy = (py - ap_y) * scale_y
                dist = math.hypot(dx, dy)
                wall_count = count_walls_between_py(ap_x, ap_y, px, py, walls)
                rssi = calculate_rssi_py(RF['TX_POWER'], dist, wall_count)
                if rssi > best_rssi:
                    best_rssi = rssi
            
            if best_rssi > RF['RSSI_THRESHOLD']:
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
        best_positions.append((
            random.randint(margin, width - margin),
            random.randint(margin, height - margin)
        ))
    
    best_coverage = evaluate_coverage(best_positions, width, height, walls)
    
    # Simulated Annealing / Random Search
    for _ in range(iterations):
        # Hacer una copia y mutar ligeramente
        candidate = []
        for (x, y) in best_positions:
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
    return [{'x': x, 'y': y} for (x, y) in best_positions]

# ============================================================
# 7. ENDPOINT: /api/optimize
# ============================================================
@app.route('/api/optimize', methods=['POST'])
def optimize():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Faltan datos'}), 400
        
        width = data.get('width', 800)
        height = data.get('height', 500)
        ap_count = data.get('ap_count', 2)
        walls = data.get('walls', [])  # El frontend enviará las paredes
        
        # Validar límites
        if ap_count < 1:
            ap_count = 1
        if ap_count > 8:
            ap_count = 8
        
        optimized = optimize_aps(width, height, ap_count, walls)
        coverage = evaluate_coverage(
            [(p['x'], p['y']) for p in optimized],
            width, height, walls
        )
        
        return jsonify({
            'aps': optimized,
            'coverage_percent': round(coverage, 2)
        })
    
    except Exception as e:
        print(f"Error en /api/optimize: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================
# 8. ENDPOINT: /api/generate_report (CON PDF Y SUPABASE)
# ============================================================
@app.route('/api/generate_report', methods=['POST'])
def generate_report():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Faltan datos'}), 400
        
        email = data.get('email', '').strip()
        industry = data.get('industry', 'No especificado')
        aps = data.get('aps', [])
        template = data.get('template', 'personalizado')
        coverage = data.get('coverage', '0%')
        avg_rssi = data.get('avgRssi', '0')
        
        # Validar email
        if not email or '@' not in email:
            return jsonify({'error': 'Correo inválido'}), 400
        
        # ============================================================
        # 8.A GUARDAR EN SUPABASE
        # ============================================================
        if supabase:
            try:
                supabase.table('leads').insert({
                    'email': email,
                    'industry': industry,
                    'template_used': template,
                    'aps_count': len(aps),
                    'coverage': coverage.replace('%', ''),   # si viene con '%', lo limpiamos
                    'avg_rssi': avg_rssi,
                    'metadata': {'aps': aps, 'version': '1.0'},
                    'product': 'rf_optimizer',   # <- Añade esta línea si no está
                    'source': 'web',             # <- o la fuente que quieras
                    'generated_at': datetime.utcnow().isoformat()
                }).execute()
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
        c.drawString(margin, y, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
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
        c.drawString(margin, y, f"📶 Cobertura estimada: {coverage} (mínimo aceptable: {RF['RSSI_THRESHOLD']} dBm)")
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
            c.drawString(margin, y, f"  AP {idx+1}: X={ap.get('x', 0):.1f}, Y={ap.get('y', 0):.1f} px")
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
        c.drawString(margin, y, "Este informe es una simulación preliminar. Para resultados exactos, solicita un Site Survey profesional.")
        c.drawRightString(width - margin, y, "v1.0 - Venezuela Insights")
        
        c.showPage()
        c.save()
        
        buffer.seek(0)
        
        # Retornar el PDF como archivo descargable
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"informe_rf_{datetime.now().strftime('%Y%m%d')}.pdf",
            mimetype='application/pdf'
        )
    
    except Exception as e:
        print(f"Error en /api/generate_report: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================
# 9. ENDPOINT DE SALUD (PARA RENDER)
# ============================================================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'supabase_connected': supabase is not None,
        'timestamp': datetime.utcnow().isoformat()
    })

# ============================================================
# 10. ARRANQUE
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)