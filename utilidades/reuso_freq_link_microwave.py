# /utilidades/reuso_freq_link_microwave.py
# -*- coding: utf-8 -*-

"""
Simulación de reuso de frecuencias en enlaces de microondas
Algoritmo de Welsh-Powell para coloración de grafos
Exporta JSON con torres y canales asignados
"""

import json
import math
import random
from collections import defaultdict

# ------------------------------
# 1. Generación de datos simulados
# ------------------------------

# Coordenadas aproximadas de Venezuela (centro en Caracas)
BASE_LAT = 10.2
BASE_LON = -67.0
RADIO_KM = 20  # radio de dispersión de torres

NUM_TOWERS = 15
AZIMUTH_RANGE = (0, 360)
BANDAS = ["5 GHz", "11 GHz", "24 GHz", "28 GHz"]

def generar_torres(n):
    torres = []
    for i in range(n):
        # Latitud y longitud aleatorias dentro del radio
        ang = random.uniform(0, 2 * math.pi)
        dist = random.uniform(0, RADIO_KM) / 111.32  # conversión a grados
        lat = BASE_LAT + dist * math.cos(ang)
        lon = BASE_LON + dist * math.sin(ang) / math.cos(math.radians(BASE_LAT))
        azimuth = random.uniform(*AZIMUTH_RANGE)
        banda = random.choice(BANDAS)
        torres.append({
            "id": i,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "azimuth": round(azimuth, 1),
            "banda": banda
        })
    return torres

# ------------------------------
# 2. Cálculo de interferencias (grafo)
# ------------------------------

DISTANCIA_UMBRAL_KM = 10.0   # umbral de distancia para interferencia
ANGULO_APERTURA_GRADOS = 30.0  # ángulo de apertura del haz (semiángulo)

def distancia_km(lat1, lon1, lat2, lon2):
    # Fórmula de Haversine
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def interferencia(torre1, torre2):
    """Determina si dos torres interfieren según distancia y orientación"""
    d = distancia_km(torre1["lat"], torre1["lon"], torre2["lat"], torre2["lon"])
    if d > DISTANCIA_UMBRAL_KM:
        return False
    # Diferencia de azimut (considerando simetría 360°)
    diff = abs(torre1["azimuth"] - torre2["azimuth"])
    diff = min(diff, 360 - diff)
    # Si ambos apuntan aproximadamente en la misma dirección, interfieren
    if diff < ANGULO_APERTURA_GRADOS:
        return True
    # También interferencia si apuntan uno hacia el otro (180° opuestos)
    # A efectos prácticos, consideramos solo misma dirección para este ejemplo
    return False

def construir_grafo(torres):
    n = len(torres)
    adyacencias = defaultdict(list)
    aristas = []
    for i in range(n):
        for j in range(i+1, n):
            if interferencia(torres[i], torres[j]):
                adyacencias[i].append(j)
                adyacencias[j].append(i)
                aristas.append((i, j))
    return adyacencias, aristas

# ------------------------------
# 3. Algoritmo de Welsh-Powell
# ------------------------------

def welsh_powell(adyacencias, n):
    """Asigna colores (canales) minimizando el número total"""
    # Ordenar nodos por grado descendente
    grados = {i: len(adyacencias[i]) for i in range(n)}
    nodos_ordenados = sorted(grados.keys(), key=lambda x: grados[x], reverse=True)
    
    color_asignado = [-1] * n  # -1 = sin color
    colores_usados = 0
    
    for nodo in nodos_ordenados:
        # Colores disponibles: buscamos el menor entero positivo no usado por vecinos
        colores_vecinos = set()
        for vecino in adyacencias[nodo]:
            if color_asignado[vecino] != -1:
                colores_vecinos.add(color_asignado[vecino])
        # Asignar el menor color disponible (empezando por 0)
        color = 0
        while color in colores_vecinos:
            color += 1
        color_asignado[nodo] = color
        colores_usados = max(colores_usados, color + 1)
    
    return color_asignado, colores_usados

# ------------------------------
# 4. Exportación a JSON
# ------------------------------

def exportar_json_estructura(torres, colores, aristas, colores_usados, archivo="datos_microondas.json"):
    # Preparar estructura para el frontend
    nodos = []
    for i, torre in enumerate(torres):
        nodos.append({
            "id": i,
            "lat": torre["lat"],
            "lon": torre["lon"],
            "azimuth": torre["azimuth"],
            "banda": torre["banda"],
            "canal": colores[i]   # canal asignado (0-indexado)
        })
    
    datos = {
        "nodos": nodos,
        "aristas": aristas,  # lista de tuplas (i, j)
        "canales_tradicionales": len(torres),  # sin reuso (cada torre un canal)
        "canales_optimizados": colores_usados
    }
    
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Datos exportados a {archivo}")
    print(f"Canales tradicionales: {datos['canales_tradicionales']}")
    print(f"Canales optimizados: {datos['canales_optimizados']}")

# ------------------------------
# Ejecución principal
# ------------------------------

if __name__ == "__main__":
    random.seed(42)  # para reproducibilidad
    torres = generar_torres(NUM_TOWERS)
    adyacencias, aristas = construir_grafo(torres)
    colores, colores_usados = welsh_powell(adyacencias, len(torres))
    exportar_json_estructura(torres, colores, aristas, colores_usados)