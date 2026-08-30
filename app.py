import os
import math
import random
import io
import json
import requests
import uuid
from datetime import datetime, timedelta

# FLASK
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

from pydantic import BaseModel, Field, validator, ValidationError

# REPORTLAB
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart

import matplotlib.pyplot as plt  # type: ignore opcional, si quieres incluir gráficos
import io as io_base
import base64
import time
from collections import defaultdict

# IMPORTAR ARCHIVOS
from utilidades.turnos_optimizer import (
    ejecutar_algoritmo_genetico,
    obtener_dias_mes,
    obtener_dias_por_tipo,
    DiaSemana,
    TipoTurno,
    BLOQUES_HORARIOS,
)
from utilidades.simulador import simular_trafico
from utilidades.backend_agrupacion import procesar_agrupacion_reportes
from utilidades.biomimetica_electrica import RedElectrica
from utilidades.reuso_freq_link_microwave import (
    generar_torres,
    construir_grafo,
    welsh_powell,
    exportar_json_estructura,
)

# SUPABASE
from supabase import create_client, Client  # type: ignore

# LOGGING
import logging

# GEMINI FLASH
import google.generativeai as genai
from google.generativeai import GenerativeModel  # type: ignore

# Configurar el logger al inicio de tu app.py
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACIÓN DE TARIFAS Y FACTORES PARA PRESUPUESTOS
# ============================================================
TARIFAS = {
    "site_survey_por_m2": 2.50,  # $/m²
    "analisis_espectro": 150.00,  # $ por sesión
    "diseno_red": 200.00,  # $ por diseño
    "instalacion_por_ap": 80.00,  # $ por AP instalado
    "costo_ap_wifi5": 180.00,  # $ por AP Wi-Fi 5
    "costo_ap_wifi6": 280.00,  # $ por AP Wi-Fi 6
    "costo_switch_poe": 350.00,  # $ por switch PoE
    "costo_cable_m": 1.50,  # $ por metro de cable CAT6
}

FACTORES_CONSTRUCCION = {
    "concreto": 1.3,  # Mayor dificultad
    "drywall": 1.0,  # Estándar
    "vidrio": 1.1,  # Reflexiones
    "mixto": 1.2,  # Combinación
}

FACTORES_ZONA = {
    "centro": 1.0,  # Tarifa base
    "periferia": 0.85,  # Menor costo
    "remota": 1.2,  # Mayor costo por desplazamiento
}

FACTORES_URGENCIA = {
    "normal": 1.0,
    "express": 1.3,  # 30% más por prioridad
}


# ============================================================
# CONFIGURACIÓN GEMINI
# ============================================================
def init_gemini_model():
    """Inicializa Gemini con compatibilidad entre distintas versiones del SDK."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY no configurada; Gemini queda deshabilitado")
        return None

    try:
        # La API moderna expone configure() en el módulo, pero algunos stubs/versión
        # de tipado no la exportan y necesitan ser llamados con getattr.
        configure = getattr(genai, "configure", None)
        if callable(configure):
            configure(api_key=api_key)
        elif hasattr(genai, "Client"):
            genai.Client(api_key=api_key)  # type: ignore
    except Exception as exc:  # pragma: no cover - log y seguir sin bloquear la app
        logger.warning(f"No se pudo configurar Gemini: {exc}")
        return None

    try:
        return genai.GenerativeModel("gemini-1.5-flash")  # type: ignore
    except Exception as exc:  # pragma: no cover
        logger.warning(f"No se pudo crear el modelo de Gemini: {exc}")
        return None


model = init_gemini_model()

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

# -------------------- INSTANCIA GLOBAL DE LA RED --------------------
red = RedElectrica()


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
# GEMINI FLASH
# ============================================================
def generar_propuesta_gemini(
    nombre_cliente, nombre_proyecto, servicios, detalles, tono
):
    """
    Genera una propuesta comercial usando Gemini Flash.
    Retorna el texto generado.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY no configurada")

    prompt = f"""
    Eres un consultor experto en tecnología y negocios. Redacta una propuesta comercial profesional para el siguiente cliente:

    Cliente: {nombre_cliente}
    Proyecto: {nombre_proyecto}
    Servicios solicitados: {servicios}
    Detalles adicionales: {detalles}
    Tono deseado: {tono} (formal, semiformal o informal)

    La propuesta debe incluir las siguientes secciones:
    1. **Introducción** (presentación de la empresa y comprensión del cliente)
    2. **Objetivos** (qué se quiere lograr)
    3. **Metodología** (cómo se abordará el proyecto)
    4. **Entregables** (qué se entregará al final)
    5. **Cronograma estimado** (plazos)
    6. **Presupuesto estimado** (incluir un rango de precios)
    7. **Próximos pasos** (cómo proceder si el cliente acepta)

    Usa un lenguaje claro, persuasivo y profesional, adaptado al tono indicado.
    """

    try:
        response = model.generate_content(prompt)  # type: ignore
        return response.text.strip()
    except Exception as e:
        raise Exception(f"Error al llamar a Gemini: {str(e)}")


# ============================================================
# FUNCIONES GENERALES
# ============================================================
def guardar_lead_en_supabase(
    email, industry, product, source, metadata, template_used=None
):
    """
    Guarda un lead en Supabase usando la API REST con returning='minimal'.
    Devuelve: (guardado_exitoso, mensaje_guardado)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, "Supabase no configurado"
    if not email:
        return False, "Email no proporcionado"

    payload = {
        "email": email,
        "industry": industry or "No especificado",
        "product": product,
        "source": source,
        "metadata": metadata or {},
        "template_used": template_used,
        "generated_at": datetime.utcnow().isoformat(),
    }
    # Remover campos None
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        response = supabase.table("leads").insert(payload, returning="minimal").execute()  # type: ignore

        # Verificar si hay error en la respuesta
        if hasattr(response, "error") and response.error:  # type: ignore
            return False, f"Error en Supabase: {response.error}"  # type: ignore

        # Si llegamos aquí, la inserción fue exitosa
        return True, "Lead guardado exitosamente"

    except Exception as e:
        return False, f"Error al guardar: {str(e)}"


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


def generar_pdf_sombras(data, email, industry):
    """
    Genera un informe PDF con los resultados del análisis de sombras.
    data: dict con keys: porcentaje_sombra, puntos_sombra, sugerencias, aps, width, height
    email, industry: datos del lead.
    Retorna: objeto BytesIO con el PDF.
    """
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.units import cm
    from datetime import datetime

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4
    margin = 50
    y = page_height - margin

    # Calcular metros cuadrados (escala fija: 20m x 12.5m para 800x500)
    width_px = data.get("width", 800)
    height_px = data.get("height", 500)
    metros_ancho = (width_px / 800) * 20
    metros_alto = (height_px / 500) * 12.5
    metros_cuadrados = round(metros_ancho * metros_alto, 1)

    # --- Título ---
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.HexColor("#0b132b"))
    c.drawString(margin, y, "Venezuela Insights - Análisis de Zonas de Sombra")
    y -= 30

    c.setFont("Helvetica", 10)
    c.setFillColor(colors.grey)
    c.drawString(margin, y, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y -= 25
    c.setFillColor(colors.black)

    # --- Datos del proyecto ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Datos del Proyecto")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Correo: {email}")
    y -= 15
    c.drawString(margin, y, f"Industria / Giro: {industry}")
    y -= 15
    c.drawString(
        margin,
        y,
        f"Dimensiones aproximadas: {metros_ancho:.1f}m x {metros_alto:.1f}m ({metros_cuadrados} m²)",
    )
    y -= 25

    # --- Resultados ---
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.HexColor("#1c2541"))
    c.drawString(margin, y, "Resultados del Análisis")
    y -= 20
    c.setFont("Helvetica", 11)
    c.drawString(
        margin,
        y,
        f"• Porcentaje de zonas de sombra: {data.get('porcentaje_sombra', 0)}%",
    )
    y -= 16
    c.drawString(
        margin, y, f"• Puntos críticos detectados: {data.get('puntos_sombra', 0)}"
    )
    y -= 16
    c.drawString(margin, y, f"• APs utilizados: {len(data.get('aps', []))}")
    y -= 20

    # --- Recomendaciones ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Recomendaciones")
    y -= 18
    c.setFont("Helvetica", 10)
    sugerencias = data.get("sugerencias", [])
    if sugerencias:
        for sug in sugerencias:
            c.drawString(
                margin,
                y,
                f"• {sug.get('justificacion', '')} (AP en X={sug.get('x', 0)}, Y={sug.get('y', 0)})",
            )
            y -= 15
    else:
        c.drawString(margin, y, "No se detectaron zonas de sombra significativas.")
        y -= 15
    y -= 10

    # --- Tarifas estimadas ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Tarifas Estimadas para Estudio Profesional")
    y -= 20

    # Tabla de tarifas
    data_table = [
        ["Concepto", "Costo (USD)"],
        ["Site Survey (por m²)", "$2.50"],
        ["Análisis de espectro", "$150.00"],
        ["Informe ejecutivo", "$200.00"],
        ["**Total estimado**", f"**${(metros_cuadrados * 2.5 + 150 + 200):.2f}**"],
    ]
    table = Table(data_table, colWidths=[3 * cm, 3 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1c2541")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 1), (-1, -2), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    table.wrapOn(c, page_width, page_height)
    table.drawOn(c, margin, y - 100)
    y -= 120

    # --- Pie de página ---
    y = margin
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawString(
        margin,
        y,
        "Este informe es una simulación preliminar. Para resultados exactos, solicita un Site Survey profesional.",
    )
    c.drawRightString(page_width - margin, y, "v1.0 - Venezuela Insights")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


def verificar_creditos(email):
    """
    Verifica cuántas generaciones ha hecho un email en las últimas 24h.
    Retorna (tiene_creditos, usos_hoy, limite)
    """
    LIMITE_DIARIO = 15
    try:
        # Buscar usos de este email en las últimas 24 horas
        hace_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        result = (
            supabase.table("leads")
            .select("id")
            .eq("email", email)
            .eq("product", "proposal_generator")
            .gte("generated_at", hace_24h)
            .execute()
        )

        usos_hoy = len(result.data) if result.data else 0
        tiene_creditos = usos_hoy < LIMITE_DIARIO

        return tiene_creditos, usos_hoy, LIMITE_DIARIO
    except Exception as e:
        print(f"Error verificando créditos: {e}")
        # En caso de error, permitir generación (fallback seguro)
        return True, 0, LIMITE_DIARIO


# ============================================================
# GENERADOR DE PROPUESTAS
# ============================================================
def generar_propuesta_template(
    nombre_cliente, nombre_proyecto, servicios, detalles, tono
):
    """Genera una propuesta profesional usando plantillas predefinidas."""

    # Mapeo de tonos
    saludos = {"formal": "Estimado(a)", "semiformal": "Hola", "informal": "¡Hola!"}
    despedidas = {
        "formal": "Quedamos a su disposición para cualquier consulta.",
        "semiformal": "Quedamos atentos a sus comentarios.",
        "informal": "¡Esperamos trabajar contigo pronto!",
    }

    saludo = saludos.get(tono, "Estimado(a)")
    despedida = despedidas.get(tono, "Quedamos a su disposición.")

    # Plantilla estructurada
    propuesta = f"""
    {saludo} {nombre_cliente},

    Nos complace presentarle nuestra propuesta para el proyecto **{nombre_proyecto}**.

    **1. Introducción**
    En Venezuela Insights, somos especialistas en soluciones tecnológicas avanzadas. Hemos identificado que {detalles or 'su organización requiere una solución integral para optimizar sus procesos.'}

    **2. Objetivos del Proyecto**
    - Implementar {servicios} para mejorar la eficiencia operativa.
    - Reducir tiempos de respuesta y optimizar recursos.
    - Garantizar la escalabilidad y seguridad de la infraestructura.

    **3. Metodología**
    Nuestro enfoque se basa en un análisis detallado de sus necesidades actuales, seguido de:
    - Fase 1: Diagnóstico y levantamiento de información.
    - Fase 2: Diseño de la solución personalizada.
    - Fase 3: Implementación y pruebas.
    - Fase 4: Capacitación y entrega final.

    **4. Entregables**
    - Informe ejecutivo con hallazgos y recomendaciones.
    - Plan de implementación detallado.
    - Documentación técnica completa.
    - Soporte post-implementación por 30 días.

    **5. Cronograma Estimado**
    El proyecto se desarrollará en un plazo de 4 a 6 semanas, dependiendo de la complejidad de los requerimientos.

    **6. Presupuesto Estimado**
    Basado en el alcance descrito, estimamos un presupuesto de entre $3,000 y $8,000 USD, el cual será ajustado según los detalles finales del proyecto.

    **7. Próximos Pasos**
    Para avanzar, le sugerimos:
    - Programar una reunión de kick-off para definir alcance final.
    - Revisar y firmar el contrato de servicios.
    - Iniciar con la fase de diagnóstico.

    {despedida}

    Atentamente,
    **Venezuela Insights**
    Equipo de Consultoría Tecnológica
    """
    return propuesta.strip()


# ============================================================
# FUNCIÓN: ANALIZAR ZONAS DE SOMBRA
# ============================================================
def realizar_analisis_sombras(aps, walls, width, height):
    """
    Analiza la cobertura RF y detecta zonas de sombra (RSSI < -70 dBm).
    Retorna:
        - porcentaje_sombra: float
        - puntos_sombra: int (número de puntos con RSSI < -70)
        - sugerencias: lista de dict con {'x': int, 'y': int, 'justificacion': str}
        - mapa_calor: matriz 2D con valores RSSI (opcional, para depuración)
    """
    step = RF["GRID_STEP"]  # 8 píxeles
    cols = max(1, width // step)
    rows = max(1, height // step)

    # Escala para convertir píxeles a metros (20m x 12.5m)
    scale_x = 20.0 / width
    scale_y = 12.5 / height

    # Matriz de RSSI
    matrix = []
    puntos_sombra = 0
    total_puntos = rows * cols

    # Para sugerencias, almacenamos las coordenadas de sombra
    sombras = []

    for r in range(rows):
        row = []
        for c in range(cols):
            px = c * step + step / 2
            py = r * step + step / 2
            real_x = px * scale_x
            real_y = py * scale_y

            best_rssi = -100
            for ap in aps:
                ap_x = ap["x"]
                ap_y = ap["y"]
                dx = (px - ap_x) * scale_x
                dy = (py - ap_y) * scale_y
                dist = math.hypot(dx, dy)
                wall_count = count_walls_between_py(ap_x, ap_y, px, py, walls)
                rssi = calculate_rssi_py(RF["TX_POWER"], dist, wall_count)
                if rssi > best_rssi:
                    best_rssi = rssi

            row.append(best_rssi)
            if best_rssi < RF["RSSI_THRESHOLD"]:  # -70 dBm
                puntos_sombra += 1
                sombras.append({"x": px, "y": py, "rssi": best_rssi})
        matrix.append(row)

    porcentaje_sombra = (puntos_sombra / total_puntos) * 100 if total_puntos > 0 else 0

    # Generar sugerencias de APs adicionales
    sugerencias = []
    if porcentaje_sombra > 15:  # Si hay más del 15% de sombra
        # Encontrar el centro de masa de las sombras
        if sombras:
            centro_x = sum(p["x"] for p in sombras) / len(sombras)
            centro_y = sum(p["y"] for p in sombras) / len(sombras)
            sugerencias.append(
                {
                    "x": round(centro_x, 1),
                    "y": round(centro_y, 1),
                    "justificacion": f"Centro de zona con sombra ({len(sombras)} puntos)",
                }
            )

            # También sugerir un segundo punto si la sombra es extensa
            if len(sombras) > 50:
                # Segundo punto en la periferia de la sombra
                # (simplificado: tomamos el punto más alejado del centro)
                max_dist = 0
                lejano = sombras[0]
                for p in sombras:
                    d = math.hypot(p["x"] - centro_x, p["y"] - centro_y)
                    if d > max_dist:
                        max_dist = d
                        lejano = p
                sugerencias.append(
                    {
                        "x": round(lejano["x"], 1),
                        "y": round(lejano["y"], 1),
                        "justificacion": "Punto adicional para cubrir extremo de la sombra",
                    }
                )

    return {
        "porcentaje_sombra": round(porcentaje_sombra, 2),
        "puntos_sombra": puntos_sombra,
        "sugerencias": sugerencias,
        "matrix": matrix,  # opcional, para depuración
    }


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
# 7 . ALGORITMO DE SALUD EMPRESARIAL (SIMULATED ANNEALING LIGERO)
# ============================================================
# -------------------- MODELOS DE ENTRADA (Pydantic) --------------------
# Seguimos usando Pydantic para validar los datos que entran al JSON de forma estricta
class DiagnosticoRequest(BaseModel):
    razon_social: str = Field(..., max_length=100)
    sector: str = Field(..., max_length=50)
    margen_bruto: float = Field(..., ge=0, le=100)
    dias_rotacion: int = Field(..., ge=1, le=365)
    costos_indexados: float = Field(..., ge=0, le=100)
    principal_dolor: str = Field(..., regex="^(liquidez|margen|precios|regulacion)$") # type:ignore

    @validator("sector")
    def validate_sector(cls, v):
        sectores_validos = [
            "comercio",
            "servicios",
            "manufactura",
            "tecnologia",
            "agroindustria",
            "otros",
        ]
        if v not in sectores_validos:
            raise ValueError(f"Sector debe ser uno de {sectores_validos}")
        return v


# -------------------- LÓGICA DE CÁLCULO --------------------
def calcular_vulnerabilidad(
    margen: float, rotacion: int, costos_idx: float, dolor: str
) -> dict:
    # Factor de inflación anualizada
    INFLACION_ANUAL = 611.0
    inflacion_mensual = (1 + INFLACION_ANUAL / 100) ** (1 / 12) - 1

    # 1. Índice base por margen
    puntaje_margen = max(0, (20 - margen) * 2.5)
    puntaje_margen = min(puntaje_margen, 50)

    # 2. Riesgo por rotación
    perdida_por_rotacion = (rotacion / 30) * inflacion_mensual * 100
    puntaje_rotacion = min(perdida_por_rotacion * 0.8, 30)

    # 3. Riesgo por costos indexados
    puntaje_costos = (costos_idx / 100) * 20

    # 4. Factor por dolor principal
    factores_dolor = {"liquidez": 1.2, "margen": 1.0, "precios": 0.8, "regulacion": 0.6}
    factor = factores_dolor.get(dolor, 1.0)

    indice_bruto = (puntaje_margen + puntaje_rotacion + puntaje_costos) * factor
    indice = min(round(indice_bruto), 100)

    # Determinar nivel y mensaje con Material Icons
    if indice >= 70:
        nivel = "rojo"
        alerta = '<span class="material-icons visual-anchor">error</span> Su flujo de caja está en riesgo crítico. La inflación y los costos indexados erosionan rápidamente su margen. Necesita acción inmediata.'
        punto_equilibrio = (
            "Recomendamos reposición cada 15 días y renegociación de costos indexados."
        )
    elif indice >= 40:
        nivel = "amarillo"
        alerta = '<span class="material-icons visual-anchor">warning_amber</span> Su empresa muestra signos de vulnerabilidad. La rotación de inventario y los costos fijos requieren ajustes estratégicos.'
        punto_equilibrio = (
            "Considere reducir días de inventario y cubrir costos en dólares."
        )
    else:
        nivel = "verde"
        alerta = '<span class="material-icons visual-anchor">check_circle</span> Su empresa goza de buena salud operativa. Monitoree la inflación y mantenga su eficiencia.'
        punto_equilibrio = "Mantenga su estrategia actual, revise precios mensualmente."

    return {
        "indice": indice,
        "nivel": nivel,
        "alerta": alerta,
        "punto_equilibrio": punto_equilibrio,
        "detalles": {
            "puntaje_margen": round(puntaje_margen, 1),
            "puntaje_rotacion": round(puntaje_rotacion, 1),
            "puntaje_costos": round(puntaje_costos, 1),
            "factor_dolor": factor,
            "inflacion_mensual_estimada": round(inflacion_mensual * 100, 2),
        },
    }


# -------------------- ENDPOINTS --------------------
@app.route("/api/v1/diagnostico", methods=["POST"])
def diagnosticar():
    try:
        # Extraer JSON de la solicitud de Flask
        json_data = request.get_json()
        if not json_data:
            return (
                jsonify(
                    {
                        "status": "error",
                        "detail": "Cuerpo de solicitud inválido o ausente",
                    }
                ),
                400,
            )

        # Validar los datos manualmente con Pydantic
        try:
            data = DiagnosticoRequest(**json_data)
        except ValidationError as e:
            return jsonify({"status": "error", "detail": e.errors()}), 422

        # 1. Calcular diagnóstico
        resultado = calcular_vulnerabilidad(
            margen=data.margen_bruto,
            rotacion=data.dias_rotacion,
            costos_idx=data.costos_indexados,
            dolor=data.principal_dolor,
        )

        # 2. Preparar registro para Supabase
        registro = {
            "razon_social": data.razon_social,
            "sector": data.sector,
            "margen_bruto": data.margen_bruto,
            "dias_rotacion": data.dias_rotacion,
            "costos_indexados": data.costos_indexados,
            "principal_dolor": data.principal_dolor,
            "indice_vulnerabilidad": resultado["indice"],
            "resultado_json": resultado,
        }

        # 3. Insertar en Supabase
        try:
            supabase.table("leads_simulador").insert(registro).execute()
            logger.info(f"Lead guardado para {data.razon_social}")
        except Exception as e:
            logger.error(f"Error al guardar en Supabase: {e}")

        # 4. Respuesta exitosa
        return (
            jsonify(
                {
                    "status": "success",
                    "diagnostico": resultado,
                    "lead_id": str(uuid.uuid4()),
                }
            ),
            200,
        )

    except Exception as e:
        logger.exception("Error interno en /diagnostico")
        return jsonify({"status": "error", "detail": str(e)}), 500


# -------------------- HEALTH CHECK (para Render) --------------------
# ============================================================
# ENDPOINT: /health
# ============================================================
@app.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok"}, 200


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
def diagnosticar_interferencias_endpoint():
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
    Devuelve: { porcentaje_sombra, puntos_sombra, sugerencias, guardado, mensaje_guardado }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        aps = data.get("aps", [])
        walls = data.get("walls", [])
        width = data.get("width", 800)
        height = data.get("height", 500)
        email = data.get("email")
        industry = data.get("industry", "No especificado")

        if not aps:
            return jsonify({"error": "No hay puntos de acceso configurados"}), 400

        # === LLAMADA A LA FUNCIÓN QUE YA TIENES ===
        resultado = realizar_analisis_sombras(aps, walls, width, height)

        # === GUARDAR LEAD EN SUPABASE (si hay email) ===
        guardado = False
        msg = "No se proporcionó email (opcional)"
        if email:
            metadata = {
                "aps": aps,
                "walls": walls,
                "width": width,
                "height": height,
                "porcentaje_sombra": resultado["porcentaje_sombra"],
                "puntos_sombra": resultado["puntos_sombra"],
                "sugerencias": resultado["sugerencias"],
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=industry,
                product="shadow_analyzer",
                source="web",
                metadata=metadata,
                template_used="shadow_analyzer",
            )

        # === RESPUESTA ===
        return jsonify(
            {
                "porcentaje_sombra": resultado["porcentaje_sombra"],
                "puntos_sombra": resultado["puntos_sombra"],
                "sugerencias": resultado["sugerencias"],
                "guardado": guardado,
                "mensaje_guardado": msg,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/analizar-sombras: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 12. PLANIFICADOR DE PRESUPUESTO
# ============================================================
@app.route("/api/generar-presupuesto", methods=["POST"])
def generar_presupuesto():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        # ============================================================
        # 1. PARÁMETROS DE ENTRADA
        # ============================================================
        tipo_proyecto = data.get(
            "tipo_proyecto", "greenfield"
        )  # greenfield | brownfield
        metros_cuadrados = float(data.get("metros_cuadrados", 0))
        num_pisos = int(data.get("num_pisos", 1))
        tipo_construccion = data.get("tipo_construccion", "mixto")
        zona = data.get("zona", "centro")
        urgencia = data.get("urgencia", "normal")
        usuarios = int(data.get("usuarios", 0))
        aplicaciones = data.get("aplicaciones", "mixto")  # streaming, voz, datos, mixto
        presupuesto_cliente = float(data.get("presupuesto", 0))  # 0 = sin límite

        email = data.get("email")
        industry = data.get("industry", "No especificado")

        # Validaciones
        if metros_cuadrados <= 0:
            return jsonify({"error": "Los metros cuadrados deben ser mayores a 0"}), 400

        # ============================================================
        # 2. CÁLCULO DE APs NECESARIOS
        # ============================================================
        # Estimación: 1 AP por cada 100-150 m² en interiores
        if tipo_construccion == "concreto":
            cobertura_por_ap = 80  # m² por AP (señal más atenuada)
        elif tipo_construccion == "vidrio":
            cobertura_por_ap = 120
        else:
            cobertura_por_ap = 100

        # Ajuste por número de pisos
        aps_por_piso = max(1, int(metros_cuadrados / cobertura_por_ap))
        aps_totales = aps_por_piso * num_pisos

        # Ajuste por densidad de usuarios (si se proporciona)
        if usuarios > 0:
            # Factor: 1 AP por cada 20-25 usuarios en oficina típica
            aps_por_usuario = max(1, int(usuarios / 25))
            aps_totales = max(aps_totales, aps_por_usuario)

        # ============================================================
        # 3. CÁLCULO DE COSTOS
        # ============================================================
        # 3.1. Servicios profesionales
        costo_site_survey = (
            metros_cuadrados
            * TARIFAS["site_survey_por_m2"]
            * FACTORES_CONSTRUCCION[tipo_construccion]
        )
        costo_analisis_espectro = (
            TARIFAS["analisis_espectro"]
            if tipo_proyecto == "brownfield"
            else TARIFAS["analisis_espectro"] * 0.5
        )
        costo_diseno_red = TARIFAS["diseno_red"] * (1 + (num_pisos - 1) * 0.3)

        # 3.2. Equipamiento (solo para greenfield o si se solicita)
        if tipo_proyecto == "greenfield":
            # Sugerir Wi-Fi 6 si hay más de 50 usuarios o presupuesto alto
            if usuarios > 50 or presupuesto_cliente > 2000:
                wifi_version = "wifi6"
                costo_ap = TARIFAS["costo_ap_wifi6"]
            else:
                wifi_version = "wifi5"
                costo_ap = TARIFAS["costo_ap_wifi5"]

            costo_equipos_ap = aps_totales * costo_ap
            # Switches PoE: 1 switch cada 4-6 APs
            num_switches = max(1, int(aps_totales / 5))
            costo_switches = num_switches * TARIFAS["costo_switch_poe"]
            # Cableado: estimación de 30m por AP (incluye subida y bajada)
            costo_cableado = aps_totales * 30 * TARIFAS["costo_cable_m"]
            costo_equipos = costo_equipos_ap + costo_switches + costo_cableado
        else:
            wifi_version = "wifi5"  # No se recomiendan equipos nuevos por defecto
            costo_equipos = 0

        # 3.3. Instalación
        costo_instalacion = aps_totales * TARIFAS["instalacion_por_ap"]

        # 3.4. Aplicar factores externos
        factor_zona = FACTORES_ZONA[zona]
        factor_urgencia = FACTORES_URGENCIA[urgencia]

        subtotal = (
            costo_site_survey
            + costo_analisis_espectro
            + costo_diseno_red
            + costo_equipos
            + costo_instalacion
        )

        # Aplicar factores
        subtotal_ajustado = subtotal * factor_zona * factor_urgencia

        # ============================================================
        # 4. APLICAR LÍMITE DE PRESUPUESTO DEL CLIENTE
        # ============================================================
        if presupuesto_cliente > 0 and subtotal_ajustado > presupuesto_cliente:
            # Ajustar recomendaciones para ajustarse al presupuesto
            recomendacion_presupuesto = (
                f"El presupuesto estimado (${subtotal_ajustado:,.2f}) excede tu límite (${presupuesto_cliente:,.2f}). "
                "Considera reducir el número de APs, usar equipos Wi-Fi 5 o realizar el proyecto por fases."
            )
            # Reducir APs proporcionalmente
            factor_ajuste = presupuesto_cliente / subtotal_ajustado
            aps_optimizados = max(1, int(aps_totales * factor_ajuste))
            # Recalcular con APs reducidos (simplificado)
            subtotal_ajustado = presupuesto_cliente * 0.95  # dejar 5% de margen
        else:
            recomendacion_presupuesto = (
                "El presupuesto estimado se encuentra dentro de tu límite establecido."
            )
            aps_optimizados = aps_totales

        # ============================================================
        # 5. GENERAR RECOMENDACIONES
        # ============================================================
        recomendaciones = [
            f"Se recomiendan {aps_optimizados} puntos de acceso para una cobertura óptima.",
            f"Tipo de construcción: {tipo_construccion}. Ajuste aplicado: {FACTORES_CONSTRUCCION[tipo_construccion]}x.",
        ]

        if tipo_proyecto == "greenfield":
            recomendaciones.append(f"Tecnología sugerida: {wifi_version.upper()}.")
        else:
            recomendaciones.append(
                "Para optimización, se recomienda un site survey detallado y análisis de espectro."
            )

        if zona == "remota":
            recomendaciones.append(
                "Zona remota: considera costos adicionales de logística y desplazamiento."
            )

        # ============================================================
        # 6. GUARDAR EN SUPABASE (usando la función auxiliar)
        # ============================================================
        guardado_exitoso = False
        mensaje_guardado = ""

        if email:
            metadata = {
                "tipo_proyecto": tipo_proyecto,
                "metros_cuadrados": metros_cuadrados,
                "num_pisos": num_pisos,
                "tipo_construccion": tipo_construccion,
                "zona": zona,
                "urgencia": urgencia,
                "usuarios": usuarios,
                "aplicaciones": aplicaciones,
                "aps_calculados": aps_optimizados,
                "presupuesto_cliente": presupuesto_cliente,
                "costo_site_survey": round(costo_site_survey, 2),
                "costo_analisis_espectro": round(costo_analisis_espectro, 2),
                "costo_diseno_red": round(costo_diseno_red, 2),
                "costo_equipos": round(costo_equipos, 2),
                "costo_instalacion": round(costo_instalacion, 2),
                "total_estimado": round(subtotal_ajustado, 2),
                "recomendaciones": recomendaciones,
                "recomendacion_presupuesto": recomendacion_presupuesto,
            }

            guardado_exitoso, mensaje_guardado = guardar_lead_en_supabase(
                email=email,
                industry=industry,
                product="budget_proposal",
                source="web",
                metadata=metadata,
                template_used="budget_proposal",
            )

        # ============================================================
        # 7. RESPUESTA
        # ============================================================
        return jsonify(
            {
                "presupuesto": {
                    "costo_site_survey": round(costo_site_survey, 2),
                    "costo_analisis_espectro": round(costo_analisis_espectro, 2),
                    "costo_diseno_red": round(costo_diseno_red, 2),
                    "costo_equipos": round(costo_equipos, 2),
                    "costo_instalacion": round(costo_instalacion, 2),
                    "subtotal": round(subtotal, 2),
                    "total_estimado": round(subtotal_ajustado, 2),
                    "factor_zona": factor_zona,
                    "factor_urgencia": factor_urgencia,
                },
                "detalles": {
                    "aps_recomendados": aps_optimizados,
                    "tipo_proyecto": tipo_proyecto,
                    "wifi_version": (
                        wifi_version if tipo_proyecto == "greenfield" else "no_aplica"
                    ),
                },
                "recomendaciones": recomendaciones,
                "recomendacion_presupuesto": recomendacion_presupuesto,
                "guardado": guardado_exitoso,
                "mensaje_guardado": mensaje_guardado,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/generar-presupuesto: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENDPOINT: /api/generar-reporte-sombras (PDF)
# ============================================================
@app.route("/api/generar-reporte-sombras", methods=["POST"])
def generar_reporte_sombras():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")

        if not email or "@" not in email:
            return jsonify({"error": "Correo inválido"}), 400

        # Preparar datos para el PDF
        pdf_data = {
            "porcentaje_sombra": data.get("porcentaje_sombra", 0),
            "puntos_sombra": data.get("puntos_sombra", 0),
            "sugerencias": data.get("sugerencias", []),
            "aps": data.get("aps", []),
            "width": data.get("width", 800),
            "height": data.get("height", 500),
        }

        pdf_buffer = generar_pdf_sombras(pdf_data, email, industry)

        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"informe_sombras_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        print(f"❌ Error en /api/generar-reporte-sombras: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENDPOINT: /api/generar-propuesta
# ============================================================
@app.route("/api/generar-propuesta", methods=["POST"])
def generar_propuesta():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        # Obtener email e industry (ahora obligatorios)
        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")

        if not email or "@" not in email:
            return jsonify({"error": "Correo obligatorio para generar propuestas"}), 400

        # Verificar créditos disponibles
        tiene_creditos, usos_hoy, limite = verificar_creditos(email)
        if not tiene_creditos:
            return (
                jsonify(
                    {
                        "error": f"Has alcanzado el límite diario de {limite} propuestas. Vuelve mañana."
                    }
                ),
                429,
            )  # Too Many Requests

        nombre_cliente = data.get("nombre_cliente", "").strip()
        nombre_proyecto = data.get("nombre_proyecto", "").strip()
        servicios = data.get("servicios", "").strip()
        detalles = data.get("detalles", "").strip()
        tono = data.get("tono", "semiformal")

        if not nombre_cliente or not nombre_proyecto or not servicios:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        # Generar propuesta con plantilla
        propuesta = generar_propuesta_template(
            nombre_cliente, nombre_proyecto, servicios, detalles, tono
        )

        # Guardar lead en Supabase (registrar el uso)
        metadata = {
            "nombre_cliente": nombre_cliente,
            "nombre_proyecto": nombre_proyecto,
            "servicios": servicios,
            "tono": tono,
            "usos_hoy": usos_hoy + 1,
            "limite_diario": limite,
        }

        guardado, msg = guardar_lead_en_supabase(
            email=email,
            industry=industry,
            product="proposal_generator",
            source="web",
            metadata=metadata,
            template_used="proposal_generator",
        )

        return jsonify(
            {
                "propuesta": propuesta,
                "titulo": f"Propuesta para {nombre_proyecto} - {nombre_cliente}",
            }
        )

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/generar-pdf-propuesta", methods=["POST"])
def generar_pdf_propuesta():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")
        propuesta_texto = data.get("propuesta", "")
        titulo = data.get("titulo", "Propuesta Comercial")

        if not email or "@" not in email:
            return jsonify({"error": "Correo inválido"}), 400
        if not propuesta_texto:
            return jsonify({"error": "No hay propuesta para generar el PDF"}), 400

        # Guardar lead en Supabase
        metadata = {
            "titulo": titulo,
            "industry": industry,
            "longitud": len(propuesta_texto),
        }
        guardado, msg = guardar_lead_en_supabase(
            email=email,
            industry=industry,
            product="proposal_generator",
            source="web",
            metadata=metadata,
            template_used="proposal_generator",
        )

        # Generar PDF
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        page_width, page_height = A4
        margin = 50
        y = page_height - margin

        # Título
        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, titulo)
        y -= 30

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.grey)
        c.drawString(margin, y, f"Generado para: {email}")
        y -= 15
        c.drawString(margin, y, f"Fecha: {datetime.now().strftime('%d/%m/%Y')}")
        y -= 25
        c.setFillColor(colors.black)

        # Cuerpo (con wrap básico)
        c.setFont("Helvetica", 11)
        lineas = propuesta_texto.split("\n")
        for linea in lineas:
            if linea.strip().startswith("**") or linea.strip().startswith("#"):
                c.setFont("Helvetica-Bold", 12)
                c.drawString(
                    margin, y, linea.strip().replace("**", "").replace("#", "")
                )
                c.setFont("Helvetica", 11)
            else:
                c.drawString(margin, y, linea)
            y -= 18
            if y < margin:
                c.showPage()
                y = page_height - margin
                c.setFont("Helvetica", 11)

        c.showPage()
        c.save()
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"propuesta_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        print(f"❌ Error en PDF: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENDPOINT: /api/optimizar-turnos
# ============================================================
@app.route("/api/optimizar-turnos", methods=["POST"])
def optimizar_turnos():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        empleados = data.get("empleados", [])
        requerimientos = data.get("requerimientos", {})
        anio = data.get("anio", 2025)
        mes = data.get("mes", 5)
        tamano_poblacion = data.get("tamano_poblacion", 30)
        generaciones = data.get("generaciones", 50)
        tasa_mutacion = data.get("tasa_mutacion", 0.3)
        email = data.get("email")
        industry = data.get("industry", "No especificado")

        # Validaciones
        if not empleados or not requerimientos:
            return jsonify({"error": "Se necesitan empleados y requerimientos"}), 400

        # Ejecutar algoritmo
        horario_optimo, costo_total = ejecutar_algoritmo_genetico(
            empleados, requerimientos, tamano_poblacion, generaciones, tasa_mutacion
        )

        # Generar planificación mensual
        dias_mes = obtener_dias_mes(anio, mes)
        planificacion_diaria = defaultdict(lambda: defaultdict(list))  # type: ignore
        for fecha, dia_semana in dias_mes:
            if dia_semana == DiaSemana.SABADO:
                tipo_turno = TipoTurno.SABADO.value
            elif dia_semana == DiaSemana.DOMINGO:
                tipo_turno = TipoTurno.DOMINGO.value
            else:
                tipo_turno = TipoTurno.LUNES_VIERNES.value
            for bloque, emp_nombres in horario_optimo.get(tipo_turno, {}).items():
                for emp in emp_nombres:
                    planificacion_diaria[fecha.strftime("%Y-%m-%d")][bloque].append(emp)

        # Resumen de horas por empleado
        horas_empleados = defaultdict(int)  # type: ignore
        dias_trabajados_por_empleado = defaultdict(set)  # type: ignore

        for tipo_turno, bloques in horario_optimo.items():
            dias = obtener_dias_por_tipo(TipoTurno(tipo_turno))  # type: ignore

            for bloque, emp_nombres in bloques.items():
                for emp in emp_nombres:
                    horas_empleados[emp] += 8 * len(dias)  # type: ignore
                    for dia in dias:  # type: ignore
                        dias_trabajados_por_empleado[emp].add(dia)

        # Guardar lead (si hay email)
        guardado = False
        msg = "No se proporcionó email (opcional)"
        if email:
            metadata = {
                "empleados": len(empleados),
                "costo_total": costo_total,
                "horario_optimo": horario_optimo,
                "mes": mes,
                "anio": anio,
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=industry,
                product="turnos_optimizer",
                source="web",
                metadata=metadata,
                template_used="turnos_optimizer",
            )

        return jsonify(
            {
                "horario_optimo": horario_optimo,
                "costo_total": round(costo_total, 2),
                "planificacion_diaria": dict(planificacion_diaria),
                "horas_por_empleado": dict(horas_empleados),
                "dias_trabajados_por_empleado": {
                    k: [d.name for d in v]
                    for k, v in dias_trabajados_por_empleado.items()
                },
                "guardado": guardado,
                "mensaje_guardado": msg,
            }
        )

    except Exception as e:
        print(f"❌ Error en /api/optimizar-turnos: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/generar-pdf-turnos", methods=["POST"])
def generar_pdf_turnos():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")
        horario_optimo = data.get("horario_optimo", {})
        costo_total = data.get("costo_total", 0)
        planificacion_diaria = data.get("planificacion_diaria", {})
        horas_por_empleado = data.get("horas_por_empleado", {})
        anio = data.get("anio", 2025)
        mes = data.get("mes", 5)

        if not email or "@" not in email:
            return jsonify({"error": "Correo inválido"}), 400

        # Generar PDF con ReportLab
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        import io

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        page_width, page_height = A4
        margin = 50
        y = page_height - margin

        # Título
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(colors.HexColor("#0b132b"))
        c.drawString(margin, y, "Informe de Optimización de Turnos")
        y -= 30

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.grey)
        c.drawString(
            margin, y, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        y -= 25
        c.setFillColor(colors.black)

        # Datos del proyecto
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Datos del Proyecto")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Correo: {email}")
        y -= 15
        c.drawString(margin, y, f"Industria: {industry}")
        y -= 15
        c.drawString(margin, y, f"Mes: {mes}/{anio}")
        y -= 25

        # Resumen económico
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.HexColor("#1c2541"))
        c.drawString(margin, y, "Resumen Económico")
        y -= 20
        c.setFont("Helvetica", 11)
        c.drawString(margin, y, f"• Costo total mensual: ${costo_total:.2f}")
        y -= 16

        # Horas por empleado
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Horas por Empleado")
        y -= 18
        c.setFont("Helvetica", 10)
        for emp, horas in horas_por_empleado.items():
            c.drawString(margin, y, f"• {emp}: {horas}h")
            y -= 14

        # Planificación (resumen)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Planificación (primeros 5 días)")
        y -= 18
        c.setFont("Helvetica", 9)
        dias_mostrados = list(planificacion_diaria.keys())[:5]
        for dia in dias_mostrados:
            c.drawString(margin, y, f"{dia}:")
            y -= 12
            for bloque, emp_list in planificacion_diaria[dia].items():
                c.drawString(margin + 20, y, f"  {bloque}: {', '.join(emp_list)}")
                y -= 12

        # Pie de página
        y = margin
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(colors.grey)
        c.drawString(
            margin,
            y,
            "Este informe fue generado automáticamente por Venezuela Insights.",
        )
        c.drawRightString(page_width - margin, y, "v1.0 - IA Insights")

        c.showPage()
        c.save()
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"informe_turnos_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        print(f"❌ Error en /api/generar-pdf-turnos: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENDPOINT: /api/simular-trafico (Simulador de Tráfico de Redes)
# ============================================================
@app.route("/api/simular-trafico", methods=["POST"])
def api_simular_trafico():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        escenario = {
            "num_aps": data.get("num_aps", 2),
            "capacidad_ap": data.get("capacidad_ap", 450),
            "num_usuarios": data.get("num_usuarios", 50),
            "tipo_trafico": data.get("tipo_trafico", "mixto"),
            "tiempo_simulacion": data.get("tiempo_simulacion", 50),
            "fallo_ap": data.get("fallo_ap", None),  # Si es None, no hay fallo
        }

        resultados = simular_trafico(escenario)

        # Guardar lead si se proporciona email (opcional)
        email = data.get("email")
        if email:
            metadata = {"escenario": escenario, "resultados": resultados}
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=data.get("industry", "No especificado"),
                product="traffic_simulator",
                source="web",
                metadata=metadata,
                template_used="traffic_simulator",
            )
        else:
            guardado = False
            msg = "No se proporcionó email (opcional)"

        return jsonify(
            {"resultados": resultados, "guardado": guardado, "mensaje_guardado": msg}
        )

    except Exception as e:
        print(f"❌ Error en /api/simular-trafico: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/generar-pdf-simulacion", methods=["POST"])
def generar_pdf_simulacion():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Faltan datos"}), 400

        email = data.get("email", "").strip()
        industry = data.get("industry", "No especificado")
        resultados = data.get("resultados", {})
        escenario = data.get("escenario", {})

        if not email or "@" not in email:
            return jsonify({"error": "Correo inválido"}), 400

        # Generar PDF con ReportLab
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        import io

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        page_width, page_height = A4
        margin = 50
        y = page_height - margin

        # Título
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(colors.HexColor("#0b132b"))
        c.drawString(margin, y, "Informe de Simulación de Tráfico de Redes")
        y -= 30

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.grey)
        c.drawString(
            margin, y, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        y -= 25
        c.setFillColor(colors.black)

        # Datos del cliente
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Datos del Proyecto")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Correo: {email}")
        y -= 15
        c.drawString(margin, y, f"Industria: {industry}")
        y -= 15
        c.drawString(margin, y, f"Fecha: {datetime.now().strftime('%d/%m/%Y')}")
        y -= 25

        # Resumen de escenario
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.HexColor("#1c2541"))
        c.drawString(margin, y, "Resumen del Escenario")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"• APs: {escenario.get('num_aps', 'N/A')}")
        y -= 15
        c.drawString(
            margin,
            y,
            f"• Capacidad por AP: {escenario.get('capacidad_ap', 'N/A')} Mbps",
        )
        y -= 15
        c.drawString(margin, y, f"• Usuarios: {escenario.get('num_usuarios', 'N/A')}")
        y -= 15
        c.drawString(
            margin, y, f"• Tipo de tráfico: {escenario.get('tipo_trafico', 'N/A')}"
        )
        y -= 15
        c.drawString(
            margin, y, f"• Fallo simulado: {escenario.get('fallo_ap', 'Sin fallo')}"
        )
        y -= 25

        # Resultados por AP
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, "Resultados por AP")
        y -= 18
        c.setFont("Helvetica", 10)
        stats = resultados.get("estadisticas", {})
        for ap, data in stats.items():
            carga_prom = data.get("promedio", 0)
            carga_max = data.get("maximo", 0)
            porcentaje = (
                round((carga_prom / escenario.get("capacidad_ap", 450)) * 100)
                if escenario.get("capacidad_ap")
                else 0
            )
            c.drawString(
                margin,
                y,
                f"AP{int(ap)+1}: Promedio {carga_prom} Mbps, Pico {carga_max} Mbps ({porcentaje}% de capacidad)",
            )
            y -= 15

        y -= 10

        # Alertas
        alertas = resultados.get("alertas", [])
        if alertas:
            c.setFont("Helvetica-Bold", 12)
            c.setFillColor(colors.red)
            c.drawString(margin, y, "Alertas Detectadas")
            y -= 18
            c.setFont("Helvetica", 10)
            c.setFillColor(colors.black)
            for alerta in alertas[:10]:  # Limitamos a 10
                c.drawString(margin, y, f"• {alerta}")
                y -= 15
        else:
            c.setFont("Helvetica-Bold", 12)
            c.setFillColor(colors.HexColor("#10b981"))
            c.drawString(margin, y, "✅ No se detectaron alertas. La red está estable.")

        # Pie de página
        y = margin
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(colors.grey)
        c.drawString(
            margin,
            y,
            "Este informe fue generado automáticamente por el Simulador de Tráfico de Redes de Venezuela Insights.",
        )
        c.drawRightString(page_width - margin, y, "v1.0 - IA Insights")

        c.showPage()
        c.save()
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"informe_simulacion_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        print(f"❌ Error en /api/generar-pdf-simulacion: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# ENDPOINT: /api/optimizar-cuadrillas (CANTV)
# ============================================================
@app.route("/api/optimizar-cuadrillas", methods=["POST"])
def agrupar_reportes():
    try:
        # Extraer cuerpo JSON (opcional: permite modificar parámetros desde el frontend)
        data = request.get_json() or {}

        num_reportes = data.get("num_reportes", 1200)
        region = data.get("region", "Región Capital")
        eps_km = data.get("eps_km", 0.2)
        min_samples = data.get("min_samples", 2)
        municipio = data.get("municipio")
        num_cuadrillas = data.get("num_cuadrillas", 10)
        priorizar = data.get("priorizar_instituciones", False)

        # Ejecutar algoritmo
        resultado = procesar_agrupacion_reportes(
            num_reportes=num_reportes,
            region=region,
            eps_km=eps_km,
            min_samples=min_samples,
            municipio=municipio,
            num_cuadrillas=num_cuadrillas,
            priorizar_instituciones=priorizar,
        )

        # Extraer datos del lead
        email = data.get("email")
        nombre = data.get("nombre")
        organismo = data.get("organismo") or data.get("empresa", "No especificado")

        # Guardar lead en Supabase si se proporciona email
        if email:
            metadata = {
                "nombre": nombre,
                "organismo": organismo,
                "parametros": {
                    "num_reportes": num_reportes,
                    "region": region,
                    "eps_km": eps_km,
                    "min_samples": min_samples,
                },
                "estadisticas": resultado.get("estadisticas"),
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=organismo,
                product="optimizar_cuadrillas",
                source="web",
                metadata=metadata,
                template_used="optimizar_cuadrillas",
            )
        else:
            guardado = False
            msg = "No se proporcionó email (opcional)"

        # Fusionar el resultado del algoritmo con el estado del guardado
        respuesta = {**resultado, "guardado": guardado, "mensaje_guardado": msg}

        return jsonify(respuesta), 200

    except Exception as e:
        print(f"❌ Error en /api/optimizar-cuadrillas: {e}")
        return (
            jsonify(
                {
                    "error": "Error interno al procesar la agrupación de reportes",
                    "detalle": str(e),
                }
            ),
            500,
        )


# ============================================================
# ENDPOINT: /api/bio-electrica (CANTV)
# ============================================================
@app.route("/api/bio-electrica/estado")
def api_estado():
    return jsonify(red.obtener_estado())


@app.route("/api/bio-electrica/paso", methods=["POST"])
def api_paso():
    data = request.get_json()
    modo = data.get("modo", "biomimetico")
    red.modo = modo
    estado = red.paso_simulacion()
    return jsonify(estado)


@app.route("/api/bio-electrica/reset", methods=["POST"])
def api_reset():
    global red
    red = RedElectrica()
    return jsonify({"status": "reset"})


# ============================================================
# ENDPOINT: /api/optimizar-reuso (Microondas)
# ============================================================
@app.route("/api/optimizar-reuso", methods=["POST"])
def optimizar_reuso():
    """
    Endpoint para optimizar el reuso de frecuencias en enlaces de microondas.
    Recibe parámetros opcionales:
        - num_torres: int (por defecto 15)
        - torres_personalizadas: lista de dicts con lat, lon, azimuth, banda (opcional)
        - email, nombre, organismo (para lead magnet)
    Devuelve:
        - nodos, aristas, canales_tradicionales, canales_optimizados
        - estado de guardado del lead
    """
    try:
        data = request.get_json() or {}

        # Parámetros de simulación
        num_torres = data.get("num_torres", 15)
        torres_personalizadas = data.get("torres_personalizadas", None)

        # Si se envían torres personalizadas, usarlas; si no, generar aleatorias
        if torres_personalizadas and len(torres_personalizadas) > 0:
            torres = torres_personalizadas
        else:
            torres = generar_torres(num_torres)

        # Construir grafo de interferencias
        adyacencias, aristas = construir_grafo(torres)

        # Aplicar Welsh‑Powell
        colores, num_colores = welsh_powell(adyacencias, len(torres))

        # Armar estructura de respuesta (similar al JSON esperado por frontend)
        nodos = []
        for i, t in enumerate(torres):
            nodos.append(
                {
                    "id": i,
                    "lat": t["lat"],
                    "lon": t["lon"],
                    "azimuth": t["azimuth"],
                    "banda": t["banda"],
                    "canal": colores[i],
                }
            )

        resultado = {
            "nodos": nodos,
            "aristas": aristas,
            "canales_tradicionales": len(torres),
            "canales_optimizados": num_colores,
        }

        # --- Captura de lead (similar al ejemplo) ---
        email = data.get("email")
        nombre = data.get("nombre")
        organismo = data.get("organismo") or data.get("empresa", "No especificado")

        guardado = False
        msg = "No se proporcionó email (opcional)"
        if email:
            metadata = {
                "nombre": nombre,
                "organismo": organismo,
                "parametros": {
                    "num_torres": num_torres,
                    "torres_personalizadas": bool(torres_personalizadas),
                },
                "estadisticas": {
                    "canales_tradicionales": len(torres),
                    "canales_optimizados": num_colores,
                },
            }
            guardado, msg = guardar_lead_en_supabase(
                email=email,
                industry=organismo,
                product="reuso_frecuencias",
                source="web",
                metadata=metadata,
                template_used="reuso_frecuencias",
            )

        respuesta = {**resultado, "guardado": guardado, "mensaje_guardado": msg}

        return jsonify(respuesta), 200

    except Exception as e:
        print(f"❌ Error en /api/optimizar-reuso: {e}")
        return (
            jsonify(
                {
                    "error": "Error interno al procesar la optimización de reuso",
                    "detalle": str(e),
                }
            ),
            500,
        )


# ============================================================
# ARRANQUE
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
