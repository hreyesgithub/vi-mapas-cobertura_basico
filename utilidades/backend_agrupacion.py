#!/usr/bin/env python3
# /utilidades/backend_agrupacion.py
# -*- coding: utf-8 -*-
"""
Script de backend para la demo de optimización de despacho de cuadrillas.
Genera datos simulados de reportes (VenApp), aplica clustering DBSCAN para
consolidar en Unidades Técnicas de Trabajo y exporta JSON para el frontend.
Incluye simulación comparativa entre estrategia tradicional y optimizada.
Autor: Senior Software Engineer (simulado)
Fecha: 2026-08-23
"""

import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
import json
import random
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

# ------------------------------------------------------------
# 1. PARÁMETROS DE SIMULACIÓN
# ------------------------------------------------------------
NUM_REPORTES = 1200  # Ajustable para demo
REGION = "Región Capital"
TIPOS_AVERIA = ["Hurto", "Clima", "Deterioro", "Vandalismo"]
FAMILIAS_AFECTADAS = list(range(1, 51))

# Centros por municipio (lat, lon)
MUNICIPIOS = {
    "Chacao": (10.4961, -66.8495),
    "Baruta": (10.4350, -66.8700),
    "Sucre": (10.4600, -66.7900),
    "Libertador": (10.5061, -66.9146),
    "El Hatillo": (10.4230, -66.8350)
}
LAT_CENTER = 10.4806
LON_CENTER = -66.9036
RADIO_GRADOS = 0.05  # ~5 km

# ------------------------------------------------------------
# 2. GENERACIÓN DE DATOS SIMULADOS
# ------------------------------------------------------------
def generar_reportes(n, region, municipio=None):
    """Genera n reportes con lat/lon, tipo de avería y familias afectadas."""
    if municipio and municipio in MUNICIPIOS:
        lat_center, lon_center = MUNICIPIOS[municipio]
    else:
        lat_center, lon_center = LAT_CENTER, LON_CENTER

    np.random.seed(42)
    random.seed(42)

    lats = np.clip(lat_center + np.random.normal(0, RADIO_GRADOS / 3, n),
                   lat_center - RADIO_GRADOS, lat_center + RADIO_GRADOS)
    lons = np.clip(lon_center + np.random.normal(0, RADIO_GRADOS / 3, n),
                   lon_center - RADIO_GRADOS, lon_center + RADIO_GRADOS)

    data = {
        "id": list(range(n)),
        "lat": lats.tolist(),
        "lon": lons.tolist(),
        "tipo_averia": [random.choice(TIPOS_AVERIA) for _ in range(n)],
        "familias_afectadas": [random.choice(FAMILIAS_AFECTADAS) for _ in range(n)],
        "region": [region] * n
    }
    return pd.DataFrame(data)

# ------------------------------------------------------------
# 3. ALGORITMO DE AGRUPACIÓN (DBSCAN)
# ------------------------------------------------------------
def consolidar_reportes(df, eps_km=0.2, min_samples=2):
    """
    Agrupa reportes por cercanía geográfica (eps_km) y mismo tipo de avería.
    Retorna un DataFrame con los nodos consolidados y estadísticas.
    """
    eps_grados = eps_km / 111.0
    coords = df[["lat", "lon"]].values

    db = DBSCAN(eps=eps_grados, min_samples=min_samples, metric='euclidean')
    clusters = db.fit_predict(coords)
    df["cluster"] = clusters

    nodos = []
    for cluster_id in set(clusters):
        if cluster_id == -1:
            for idx, row in df[df.cluster == -1].iterrows():
                nodos.append({
                    "id_nodo": f"NODO-{len(nodos)+1}",
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "tipo_averia": row["tipo_averia"],
                    "num_reportes": 1,
                    "familias_totales": row["familias_afectadas"],
                    "reportes_ids": [row["id"]],
                    "es_consolidado": False
                })
        else:
            grupo = df[df.cluster == cluster_id]
            lat_centro = grupo["lat"].mean()
            lon_centro = grupo["lon"].mean()
            tipo_comun = grupo["tipo_averia"].mode()[0]
            num_reportes = len(grupo)
            familias_totales = grupo["familias_afectadas"].sum()
            reportes_ids = grupo["id"].tolist()

            nodos.append({
                "id_nodo": f"NODO-{len(nodos)+1}",
                "lat": lat_centro,
                "lon": lon_centro,
                "tipo_averia": tipo_comun,
                "num_reportes": num_reportes,
                "familias_totales": familias_totales,
                "reportes_ids": reportes_ids,
                "es_consolidado": True
            })

    return pd.DataFrame(nodos)

# ------------------------------------------------------------
# 4. FUNCIONES DE SIMULACIÓN COMPARATIVA
# ------------------------------------------------------------
def haversine(coord1, coord2):
    """Distancia en kilómetros entre dos puntos (lat, lon)."""
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def calcular_metricas_simulacion(df_reportes, df_nodos, num_cuadrillas=10, priorizar_instituciones=False):
    """
    Calcula métricas comparativas para estrategia tradicional vs optimizada.
    Retorna dict con claves: tradicional, optimizada, ahorro.
    """
    # --- Estrategia tradicional: cada reporte es una orden ---
    ordenes_trad = len(df_reportes)
    # Simular priorización (aleatoria o priorizando instituciones)
    if priorizar_instituciones:
        # Asignar prioridad aleatoria (en un caso real, se usarían datos de instituciones)
        df_reportes['prioridad'] = np.random.rand(len(df_reportes))
        df_trad = df_reportes.sort_values('prioridad', ascending=False)
    else:
        df_trad = df_reportes.sample(frac=1, random_state=42)

    coords_trad = df_trad[['lat', 'lon']].values
    dist_trad = 0
    for i in range(1, len(coords_trad)):
        dist_trad += haversine(coords_trad[i-1], coords_trad[i])
    tiempo_trad = dist_trad / 30  # velocidad promedio 30 km/h

    # --- Estrategia optimizada: nodos consolidados ---
    ordenes_opt = len(df_nodos)
    coords_opt = df_nodos[['lat', 'lon']].values
    dist_opt = 0
    for i in range(1, len(coords_opt)):
        dist_opt += haversine(coords_opt[i-1], coords_opt[i])
    tiempo_opt = dist_opt / 30

    ahorro_ordenes = ordenes_trad - ordenes_opt
    ahorro_dist = dist_trad - dist_opt
    ahorro_tiempo = tiempo_trad - tiempo_opt

    return {
        "tradicional": {
            "ordenes": ordenes_trad,
            "distancia_km": round(dist_trad, 2),
            "tiempo_hrs": round(tiempo_trad, 2)
        },
        "optimizada": {
            "ordenes": ordenes_opt,
            "distancia_km": round(dist_opt, 2),
            "tiempo_hrs": round(tiempo_opt, 2)
        },
        "ahorro": {
            "ordenes": ahorro_ordenes,
            "distancia_km": round(ahorro_dist, 2),
            "tiempo_hrs": round(ahorro_tiempo, 2),
            "porcentaje_ordenes": round(ahorro_ordenes / ordenes_trad * 100, 2) if ordenes_trad else 0
        }
    }

# ------------------------------------------------------------
# 5. GENERACIÓN DE JSON DE SALIDA
# ------------------------------------------------------------
def generar_json_salida(df_reportes, df_nodos, region):
    """Construye el objeto JSON base."""
    total_reportes = len(df_reportes)
    total_nodos = len(df_nodos)
    nodos_consolidados = df_nodos[df_nodos.es_consolidado == True].shape[0]
    reduccion = total_reportes - total_nodos

    reportes_list = df_reportes[["id", "lat", "lon", "tipo_averia", "familias_afectadas"]].to_dict(orient="records")
    nodos_list = df_nodos.to_dict(orient="records")

    return {
        "region": region,
        "estadisticas": {
            "total_reportes": total_reportes,
            "nodos_generados": total_nodos,
            "nodos_consolidados": nodos_consolidados,
            "reduccion_duplicados": reduccion,
            "porcentaje_reduccion": round((reduccion / total_reportes) * 100, 2) if total_reportes > 0 else 0
        },
        "reportes": reportes_list,
        "nodos": nodos_list,
        "generado_en": datetime.now().isoformat()
    }

# ------------------------------------------------------------
# 6. FUNCIÓN ORQUESTADORA PRINCIPAL
# ------------------------------------------------------------
def procesar_agrupacion_reportes(num_reportes=1200, region="Región Capital", eps_km=0.2, min_samples=2,
                                 municipio=None, num_cuadrillas=10, priorizar_instituciones=False):
    """Función invocada por el endpoint de Flask."""
    df_reportes = generar_reportes(num_reportes, region, municipio)
    df_nodos = consolidar_reportes(df_reportes, eps_km, min_samples)
    resultado = generar_json_salida(df_reportes, df_nodos, region)

    # Añadir simulación comparativa
    metricas = calcular_metricas_simulacion(df_reportes, df_nodos, num_cuadrillas, priorizar_instituciones)
    resultado['simulacion'] = metricas

    return resultado

# ------------------------------------------------------------
# 7. EJECUCIÓN LOCAL (para pruebas)
# ------------------------------------------------------------
if __name__ == "__main__":
    print("Generando reportes simulados...")
    df_reportes = generar_reportes(1200, "Región Capital")
    print(f"Se generaron {len(df_reportes)} reportes.")

    print("Aplicando clustering DBSCAN (eps=200m, min_samples=2)...")
    df_nodos = consolidar_reportes(df_reportes, eps_km=0.2, min_samples=2)
    print(f"Se generaron {len(df_nodos)} Unidades Técnicas de Trabajo.")

    print("Construyendo JSON de salida...")
    data_json = procesar_agrupacion_reportes(
        num_reportes=1200,
        region="Región Capital",
        eps_km=0.2,
        min_samples=2,
        municipio="Chacao",
        num_cuadrillas=10,
        priorizar_instituciones=True
    )

    with open("datos_consolidados.json", "w", encoding="utf-8") as f:
        json.dump(data_json, f, indent=2, ensure_ascii=False)

    print("JSON exportado correctamente a 'datos_consolidados.json'.")
    print("Estadísticas:")
    print(f" - Total reportes: {data_json['estadisticas']['total_reportes']}")
    print(f" - Nodos generados: {data_json['estadisticas']['nodos_generados']}")
    print(f" - Reducción: {data_json['estadisticas']['reduccion_duplicados']} ({data_json['estadisticas']['porcentaje_reduccion']}%)")
    if 'simulacion' in data_json:
        sim = data_json['simulacion']
        print("Simulación:")
        print(f" - Tradicional: {sim['tradicional']['ordenes']} órdenes, {sim['tradicional']['distancia_km']} km, {sim['tradicional']['tiempo_hrs']} h")
        print(f" - Optimizada: {sim['optimizada']['ordenes']} órdenes, {sim['optimizada']['distancia_km']} km, {sim['optimizada']['tiempo_hrs']} h")
        print(f" - Ahorro: {sim['ahorro']['porcentaje_ordenes']}% en órdenes")