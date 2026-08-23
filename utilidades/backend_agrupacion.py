#!/usr/bin/env python3
# /utilidades/backend_agrupacion.py
# -*- coding: utf-8 -*-
"""
Script de backend para la demo de optimización de despacho de cuadrillas.
Genera datos simulados de reportes (VenApp), aplica clustering DBSCAN para
consolidar en Unidades Técnicas de Trabajo y exporta JSON para el frontend.
Autor: Senior Software Engineer (simulado)
Fecha: 2026-08-23
"""

import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
import json
import random
from datetime import datetime

# ------------------------------------------------------------
# 1. PARÁMETROS DE SIMULACIÓN (basados en métricas reales de CANTV)
# ------------------------------------------------------------
NUM_REPORTES = 1200  # Ajustable para demo (escala de 54k o 70k)
REGION = "Región Capital"  # o "Los Llanos"
TIPOS_AVERIA = ["Hurto", "Clima", "Deterioro", "Vandalismo"]
FAMILIAS_AFECTADAS = list(range(1, 51))  # de 1 a 50 familias

# Centro aproximado de Caracas (para simular densidad)
LAT_CENTER = 10.4806
LON_CENTER = -66.9036
RADIO_GRADOS = 0.05  # ~5 km de radio

# ------------------------------------------------------------
# 2. GENERACIÓN DE DATOS SIMULADOS
# ------------------------------------------------------------
def generar_reportes(n, region):
    """Genera n reportes con lat/lon, tipo de avería y familias afectadas."""
    np.random.seed(42)
    random.seed(42)

    data = {
        "id": list(range(n)),
        "lat": np.clip(LAT_CENTER + np.random.normal(0, RADIO_GRADOS / 3, n), LAT_CENTER - RADIO_GRADOS, LAT_CENTER + RADIO_GRADOS).tolist(),
        "lon": np.clip(LON_CENTER + np.random.normal(0, RADIO_GRADOS / 3, n), LON_CENTER - RADIO_GRADOS, LON_CENTER + RADIO_GRADOS).tolist(),
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
    # Convertir km a grados (aprox 1 grado = 111 km)
    eps_grados = eps_km / 111.0

    # Seleccionar características para clustering: lat, lon (normalizar opcional)
    coords = df[["lat", "lon"]].values

    # Escalar para que DBSCAN funcione mejor (opcional, pero con eps en grados no es necesario)
    # scaler = StandardScaler()
    # coords_scaled = scaler.fit_transform(coords)
    # Usamos coordenadas directamente en grados

    # Aplicar DBSCAN
    db = DBSCAN(eps=eps_grados, min_samples=min_samples, metric='euclidean')
    clusters = db.fit_predict(coords)

    # Agregar etiqueta de clúster al DataFrame original
    df["cluster"] = clusters

    # Identificar ruido (-1) y asignarles un cluster único (cada uno su propio nodo)
    # Pero para la demo, trataremos cada ruido como un nodo individual (no consolidado)
    # Para los clusters válidos (>=0), calculamos centroide y agregamos info

    # Agrupar por cluster
    nodos = []
    for cluster_id in set(clusters):
        if cluster_id == -1:
            # Cada punto ruidoso es un nodo individual
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
            # Clúster consolidado
            grupo = df[df.cluster == cluster_id]
            lat_centro = grupo["lat"].mean()
            lon_centro = grupo["lon"].mean()
            # Obtener el tipo de avería más frecuente en el grupo
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

    df_nodos = pd.DataFrame(nodos)
    return df_nodos

# ------------------------------------------------------------
# 4. ESTADÍSTICAS Y EXPORTACIÓN A JSON
# ------------------------------------------------------------
def generar_json_salida(df_reportes, df_nodos, region):
    """Construye el objeto JSON con datos para el frontend."""
    total_reportes = len(df_reportes)
    total_nodos = len(df_nodos)
    nodos_consolidados = df_nodos[df_nodos.es_consolidado == True].shape[0]
    reduccion = total_reportes - total_nodos

    # Preparar datos para el mapa (puntos de reportes individuales y nodos)
    reportes_list = df_reportes[["id", "lat", "lon", "tipo_averia", "familias_afectadas"]].to_dict(orient="records")
    nodos_list = df_nodos.to_dict(orient="records")

    output = {
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
    return output

def procesar_agrupacion_reportes(num_reportes=1200, region="Región Capital", eps_km=0.2, min_samples=2):
    """Función orquestadora invocada por el endpoint de Flask."""
    df_reportes = generar_reportes(num_reportes, region)
    df_nodos = consolidar_reportes(df_reportes, eps_km, min_samples)
    return generar_json_salida(df_reportes, df_nodos, region)

# ------------------------------------------------------------
# 5. EJECUCIÓN PRINCIPAL
# ------------------------------------------------------------
if __name__ == "__main__":
    print("Generando reportes simulados...")
    df_reportes = generar_reportes(NUM_REPORTES, REGION)
    print(f"Se generaron {len(df_reportes)} reportes.")

    print("Aplicando clustering DBSCAN (eps=200m, min_samples=2)...")
    df_nodos = consolidar_reportes(df_reportes, eps_km=0.2, min_samples=2)
    print(f"Se generaron {len(df_nodos)} Unidades Técnicas de Trabajo.")

    print("Construyendo JSON de salida...")
    data_json = generar_json_salida(df_reportes, df_nodos, REGION)

    # Guardar en archivo
    with open("datos_consolidados.json", "w", encoding="utf-8") as f:
        json.dump(data_json, f, indent=2, ensure_ascii=False)

    print("JSON exportado correctamente a 'datos_consolidados.json'.")
    print("Estadísticas:")
    print(f" - Total reportes: {data_json['estadisticas']['total_reportes']}")
    print(f" - Nodos generados: {data_json['estadisticas']['nodos_generados']}")
    print(f" - Reducción de duplicados: {data_json['estadisticas']['reduccion_duplicados']} ({data_json['estadisticas']['porcentaje_reduccion']}%)")