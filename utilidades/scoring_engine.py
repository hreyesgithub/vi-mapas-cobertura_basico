"""
scoring_engine.py
==================
Motor de cálculo de "Venezuela Insights Score" (VIS).

Convergen tres dominios en un único índice de riesgo/oportunidad para un activo
inmobiliario en Venezuela:

1. SRI  (Signal Resilience Index)      -> Mapeo técnico de infraestructura/telecom
2. ERI  (Energy Resilience Index)      -> Eficiencia energética / física aplicada
3. VIS  (Composite Real Estate Score)  -> Inteligencia de mercado inmobiliario

Todas las fórmulas están documentadas con su fundamento físico/técnico para que
el resultado no sea una "caja negra": cada número que ve el usuario final puede
explicarse con la ecuación que lo produjo.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Datos de referencia por ciudad
#
#    Dos fuentes conviven aquí, y cada ciudad queda marcada con cuál usa:
#
#    - "real": base_value_usd_m2, muestra y rentabilidad (cap rate) vienen de
#      listings reales de Mercado Libre Venezuela (Ventas/Alquileres),
#      agregados por data/ingest_market_data.py -> data/market_reference.json.
#      Telecom/energía siguen siendo estimados (ese dataset no los cubre),
#      heredados del perfil promedio del área metropolitana de Caracas.
#    - "estimado": ciudades fuera del dataset real (aún no hay listings
#      cargados para Valencia, Maracaibo, etc.). Todo el perfil es una
#      aproximación con fines demostrativos, a reemplazar cuando existan
#      datos reales para esas ciudades.
# ---------------------------------------------------------------------------

# Perfil telecom/energía promedio del Área Metropolitana de Caracas, usado
# como fallback para los municipios con datos reales de precio pero sin
# datos reales de infraestructura/energía propios.
_CARACAS_INFRA_DEFAULTS = {
    "avg_outage_hours_month": 6,
    "solar_irradiance_kwh_m2_day": 5.1,
    "fiber_backbone_distance_km": 1.2,
    "tower_density_km2": 4.0,
}

# Coordenadas aproximadas por municipio del Área Metropolitana de Caracas.
_CARACAS_MUNICIPIO_COORDS = {
    "baruta": (10.4380, -66.8756),
    "chacao": (10.4989, -66.8535),
    "el_hatillo": (10.3859, -66.8228),
    "sucre": (10.4806, -66.8092),
    "libertador": (10.5000, -66.9170),
    "distrito_capital": (10.4989, -66.9036),
    "caracas": (10.4989, -66.8535),
}

_MARKET_DATA_PATH = Path(__file__).parent / "data" / "market_reference.json"
_SOLAR_DATA_PATH = Path(__file__).parent / "data" / "solar_irradiance.json"
_GRID_SEVERITY_PATH = Path(__file__).parent / "data" / "grid_severity_index.json"


def _load_real_market_cities() -> dict:
    """Carga data/market_reference.json (generado por ingest_market_data.py)
    y construye entradas de CITY_REFERENCE_DATA con datos reales de precio."""
    if not _MARKET_DATA_PATH.exists():
        return {}

    raw = json.loads(_MARKET_DATA_PATH.read_text(encoding="utf-8"))
    cities = {}
    for key, zona in raw.get("zonas", {}).items():
        lat, lon = _CARACAS_MUNICIPIO_COORDS.get(key, _CARACAS_MUNICIPIO_COORDS["caracas"])
        cities[key] = {
            "label": f"{zona['label']} (Caracas)",
            "lat": lat, "lon": lon,
            "base_value_usd_m2": zona["ventas_precio_m2_mediana"],
            **_CARACAS_INFRA_DEFAULTS,
            # Metadatos de mercado real, expuestos en la respuesta de la API
            "data_source": "real",
            "market_sample_size": zona["ventas_muestra"],
            "market_price_p25_usd_m2": zona["ventas_precio_m2_p25"],
            "market_price_p75_usd_m2": zona["ventas_precio_m2_p75"],
            "market_rental_price_usd_m2": zona.get("alquiler_precio_m2_mediana"),
            "market_rental_yield_annual_pct": zona.get("rental_yield_anual_pct"),
        }
    return cities


_ESTIMATED_CITY_DATA = {
    "valencia": {
        "label": "Valencia (Carabobo)",
        "lat": 10.1620, "lon": -68.0077,
        "base_value_usd_m2": 780,
        "avg_outage_hours_month": 22,
        "solar_irradiance_kwh_m2_day": 5.4,
        "fiber_backbone_distance_km": 3.5,
        "tower_density_km2": 2.8,
        "data_source": "estimado",
    },
    "maracaibo": {
        "label": "Maracaibo (Zulia)",
        "lat": 10.6427, "lon": -71.6125,
        "base_value_usd_m2": 520,
        "avg_outage_hours_month": 48,
        "solar_irradiance_kwh_m2_day": 5.9,
        "fiber_backbone_distance_km": 4.8,
        "tower_density_km2": 1.9,
        "data_source": "estimado",
    },
    "maracay": {
        "label": "Maracay (Aragua)",
        "lat": 10.2469, "lon": -67.5959,
        "base_value_usd_m2": 690,
        "avg_outage_hours_month": 18,
        "solar_irradiance_kwh_m2_day": 5.3,
        "fiber_backbone_distance_km": 2.9,
        "tower_density_km2": 3.1,
        "data_source": "estimado",
    },
    "margarita": {
        "label": "Isla Margarita (Nueva Esparta)",
        "lat": 11.0000, "lon": -64.0000,
        "base_value_usd_m2": 1100,
        "avg_outage_hours_month": 14,
        "solar_irradiance_kwh_m2_day": 6.1,
        "fiber_backbone_distance_km": 6.5,
        "tower_density_km2": 2.2,
        "data_source": "estimado",
    },
    "barquisimeto": {
        "label": "Barquisimeto (Lara)",
        "lat": 10.0678, "lon": -69.3474,
        "base_value_usd_m2": 610,
        "avg_outage_hours_month": 26,
        "solar_irradiance_kwh_m2_day": 5.6,
        "fiber_backbone_distance_km": 3.9,
        "tower_density_km2": 2.4,
        "data_source": "estimado",
    },
}

# Fusión final: datos reales primero (si el JSON de mercado existe y trae
# zonas), luego las ciudades aún no cubiertas por datos reales. Si en el
# futuro llegan listings reales de Valencia/Maracaibo/etc., basta con que
# ingest_market_data.py produzca esas claves y automáticamente reemplazan
# (o conviven con) estas entradas estimadas.
def _load_real_solar_irradiance() -> dict:
    """Carga data/solar_irradiance.json (generado por fetch_solar_irradiance.py,
    fuente: NASA POWER). Si el archivo no existe (nunca se corrió el script, o
    no hubo acceso de red al generarlo), retorna {} y todas las ciudades
    conservan su irradiancia ESTIMADA sin romper nada."""
    if not _SOLAR_DATA_PATH.exists():
        return {}
    try:
        raw = json.loads(_SOLAR_DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw.get("cities", {})


def _load_grid_severity_index() -> dict:
    """Carga data/grid_severity_index.json (generado por
    build_grid_severity_index.py, fuente: encuestas OVSP + prensa citada).
    Retorna {} si no existe, dejando el avg_outage_hours_month ESTIMADO
    intacto en todas las ciudades."""
    if not _GRID_SEVERITY_PATH.exists():
        return {}
    try:
        raw = json.loads(_GRID_SEVERITY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    estados = raw.get("estados", {})
    city_to_estado = raw.get("city_to_estado", {})
    per_city = {}
    for city_key, estado_key in city_to_estado.items():
        estado = estados.get(estado_key)
        if estado:
            per_city[city_key] = {
                "avg_outage_hours_month": estado["avg_outage_hours_month"],
                "tier": estado["tier"],
                "estado_label": estado["label"],
                "basis": estado["basis"],
            }
    return per_city


CITY_REFERENCE_DATA = {**_ESTIMATED_CITY_DATA, **_load_real_market_cities()}

# Sobrescribe la irradiancia solar ESTIMADA con el dato REAL de NASA POWER en
# cualquier ciudad para la que exista (real-estate "real" o "estimado" son
# independientes de si la irradiancia es real o estimada: son dos ejes de
# calidad de dato distintos, cada ciudad puede tener 0, 1 o 2 en real).
_real_solar = _load_real_solar_irradiance()
for _key, _city in CITY_REFERENCE_DATA.items():
    _solar_entry = _real_solar.get(_key)
    if _solar_entry:
        _city["solar_irradiance_kwh_m2_day"] = _solar_entry["solar_irradiance_kwh_m2_day"]
        _city["solar_data_source"] = "real"
        _city["solar_data_source_detail"] = _solar_entry["source"]
    else:
        _city.setdefault("solar_data_source", "estimado")

# Sobrescribe las horas de corte ESTIMADAS con el índice PROXY (OVSP + prensa
# citada) cuando la ciudad tiene un estado mapeado. Es un tercer nivel de
# calidad de dato, ni "real" (medición directa) ni "estimado" (sin respaldo):
# es trazable a fuentes con fecha, pero sigue siendo una aproximación.
_grid_severity = _load_grid_severity_index()
for _key, _city in CITY_REFERENCE_DATA.items():
    _grid_entry = _grid_severity.get(_key)
    if _grid_entry:
        _city["avg_outage_hours_month"] = _grid_entry["avg_outage_hours_month"]
        _city["grid_data_source"] = "proxy_ovsp"
        _city["grid_severity_tier"] = _grid_entry["tier"]
        _city["grid_data_source_detail"] = f"{_grid_entry['estado_label']}: {_grid_entry['basis']}"
    else:
        _city.setdefault("grid_data_source", "estimado")


# ---------------------------------------------------------------------------
# 2. Modelos de entrada
# ---------------------------------------------------------------------------

@dataclass
class ScoreRequest:
    city_key: str
    distance_to_tower_km: float          # distancia a la torre celular más cercana
    frequency_mhz: float = 1900.0        # banda típica LTE/4G
    fiber_backbone_distance_km: Optional[float] = None
    avg_outage_hours_month: Optional[float] = None
    panel_area_m2: float = 20.0          # área disponible para paneles solares
    panel_efficiency: float = 0.20       # eficiencia típica panel monocristalino
    daily_consumption_kwh: float = 15.0  # consumo diario estimado del inmueble
    property_area_m2: float = 120.0
    property_type: str = "residencial"   # residencial | comercial | industrial


# ---------------------------------------------------------------------------
# 3. Sub-modelo 1: Signal Resilience Index (SRI)
#    Fundamento: modelo de pérdida de trayecto en espacio libre (FSPL),
#    ecuación estándar de telecomunicaciones:
#
#        FSPL(dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44
#
#    A mayor FSPL, peor señal esperada. Se normaliza a un score 0-100 y se
#    combina con la proximidad a backbone de fibra óptica (latencia/backhaul).
# ---------------------------------------------------------------------------

def compute_fspl_db(distance_km: float, freq_mhz: float) -> float:
    d = max(distance_km, 0.01)  # evita log(0)
    return 20 * math.log10(d) + 20 * math.log10(freq_mhz) + 32.44


def compute_signal_resilience_index(
    distance_to_tower_km: float,
    freq_mhz: float,
    fiber_backbone_distance_km: float,
    tower_density_km2: float,
) -> dict:
    fspl_db = compute_fspl_db(distance_to_tower_km, freq_mhz)

    # Rango práctico observado en despliegues urbanos/rurales: ~80dB (excelente)
    # a ~150dB (borde de cobertura). Se normaliza linealmente e invertido.
    fspl_score = max(0.0, min(100.0, (150 - fspl_db) / (150 - 80) * 100))

    # Penalización por distancia a backbone de fibra (afecta backhaul/latencia).
    # Modelo logístico simple: decae con la distancia.
    fiber_score = 100 / (1 + (fiber_backbone_distance_km / 5.0) ** 2)

    # Bonus por densidad de torres (redundancia de red = resiliencia ante fallas).
    density_bonus = min(15.0, tower_density_km2 * 3.0)

    sri = max(0.0, min(100.0, 0.55 * fspl_score + 0.35 * fiber_score + 0.10 * density_bonus))

    return {
        "score": round(sri, 1),
        "fspl_db": round(fspl_db, 2),
        "fspl_component": round(fspl_score, 1),
        "fiber_component": round(fiber_score, 1),
        "density_bonus": round(density_bonus, 1),
        "explanation": (
            f"Pérdida de trayecto (FSPL) estimada en {round(fspl_db, 1)} dB a "
            f"{distance_to_tower_km} km de la torre más cercana, banda {int(freq_mhz)} MHz."
        ),
    }


# ---------------------------------------------------------------------------
# 4. Sub-modelo 2: Energy Resilience Index (ERI)
#    Fundamento: física aplicada de sistemas fotovoltaicos.
#
#        E_solar (kWh/día) = Irradiancia (kWh/m²/día) * Área (m²)
#                             * Eficiencia_panel * Performance_Ratio
#
#    Performance Ratio (PR) ~0.75-0.80 en instalaciones reales (pérdidas por
#    temperatura, cableado, inversor, suciedad).
#
#    Autonomía de respaldo (horas) = (E_solar_día / consumo_diario) * 24
# ---------------------------------------------------------------------------

PERFORMANCE_RATIO = 0.78  # estándar de la industria fotovoltaica

def compute_energy_resilience_index(
    avg_outage_hours_month: float,
    solar_irradiance_kwh_m2_day: float,
    panel_area_m2: float,
    panel_efficiency: float,
    daily_consumption_kwh: float,
) -> dict:
    solar_generation_kwh_day = (
        solar_irradiance_kwh_m2_day * panel_area_m2 * panel_efficiency * PERFORMANCE_RATIO
    )

    coverage_ratio = solar_generation_kwh_day / max(daily_consumption_kwh, 0.1)
    coverage_score = min(100.0, coverage_ratio * 100)

    # Penalización por horas de corte mensuales (máx. práctico observado ~120h/mes)
    outage_penalty = min(100.0, (avg_outage_hours_month / 120.0) * 100)
    grid_stability_score = 100 - outage_penalty

    # Autonomía estimada si se respalda con baterías dimensionadas al consumo diario
    autonomy_hours = (solar_generation_kwh_day / max(daily_consumption_kwh, 0.1)) * 24

    eri = max(0.0, min(100.0, 0.5 * grid_stability_score + 0.5 * coverage_score))

    return {
        "score": round(eri, 1),
        "solar_generation_kwh_day": round(solar_generation_kwh_day, 2),
        "coverage_ratio_pct": round(coverage_score, 1),
        "grid_stability_score": round(grid_stability_score, 1),
        "estimated_backup_autonomy_hours": round(autonomy_hours, 1),
        "explanation": (
            f"Un sistema solar de {panel_area_m2} m² a {panel_efficiency*100:.0f}% de eficiencia "
            f"generaría ~{round(solar_generation_kwh_day, 1)} kWh/día, cubriendo "
            f"{round(coverage_score, 0)}% del consumo estimado del inmueble."
        ),
    }


# ---------------------------------------------------------------------------
# 5. Sub-modelo 3: Composite Real Estate Score (valuación ajustada por riesgo)
#
#        Valor_ajustado = Valor_base * (1 + w_t*(SRI/100 - 0.5)
#                                           + w_e*(ERI/100 - 0.5))
#
#    Los índices de infraestructura y energía actúan como multiplicadores de
#    riesgo/prima sobre el valor base de mercado por m².
# ---------------------------------------------------------------------------

PROPERTY_TYPE_WEIGHTS = {
    # (peso_telecom, peso_energia) — un local comercial pondera más la
    # conectividad; una vivienda pondera más la energía.
    "residencial": (0.12, 0.18),
    "comercial": (0.22, 0.12),
    "industrial": (0.10, 0.28),
}


def compute_market_confidence(data_source: str, sample_size: Optional[int]) -> dict:
    """Indicador de confianza del valor base usado: 'real' con buena muestra
    pesa más que 'estimado'. No mueve el VIS score (que ya pondera SRI/ERI),
    pero se expone para que el usuario sepa cuánto confiar en el valor base."""
    if data_source != "real" or not sample_size:
        return {"nivel": "estimado", "muestra": sample_size, "detalle": "Valor de referencia aproximado; aún sin listings reales cargados para esta zona."}
    if sample_size >= 30:
        nivel = "alta"
    elif sample_size >= 10:
        nivel = "media"
    else:
        nivel = "baja"
    return {
        "nivel": nivel,
        "muestra": sample_size,
        "detalle": f"Mediana calculada sobre {sample_size} listings reales (Mercado Libre Venezuela).",
    }


def compute_composite_score(
    base_value_usd_m2: float,
    property_area_m2: float,
    property_type: str,
    sri_score: float,
    eri_score: float,
    city_meta: dict,
) -> dict:
    w_telecom, w_energy = PROPERTY_TYPE_WEIGHTS.get(property_type, (0.15, 0.15))

    adjustment_factor = 1 + w_telecom * (sri_score / 100 - 0.5) + w_energy * (eri_score / 100 - 0.5)
    adjusted_value_usd_m2 = base_value_usd_m2 * adjustment_factor
    total_estimated_value = adjusted_value_usd_m2 * property_area_m2

    vis_score = round(0.4 * sri_score + 0.4 * eri_score + 0.2 * min(100, adjustment_factor * 100), 1)

    if vis_score >= 75:
        tier = "Alta Resiliencia"
    elif vis_score >= 50:
        tier = "Resiliencia Moderada"
    else:
        tier = "Riesgo Elevado"

    data_source = city_meta.get("data_source", "estimado")
    sample_size = city_meta.get("market_sample_size")

    result = {
        "vis_score": vis_score,
        "tier": tier,
        "base_value_usd_m2": base_value_usd_m2,
        "adjusted_value_usd_m2": round(adjusted_value_usd_m2, 2),
        "adjustment_factor_pct": round((adjustment_factor - 1) * 100, 2),
        "total_estimated_value_usd": round(total_estimated_value, 2),
        "weights_used": {"telecom": w_telecom, "energy": w_energy},
        "market_data_source": data_source,
        "market_confidence": compute_market_confidence(data_source, sample_size),
    }

    # Metadatos de mercado real (rango de precios y rentabilidad de alquiler),
    # solo presentes cuando la ciudad tiene datos reales cargados.
    if data_source == "real":
        result["market_price_range_usd_m2"] = {
            "p25": city_meta.get("market_price_p25_usd_m2"),
            "p75": city_meta.get("market_price_p75_usd_m2"),
        }
        result["market_rental_price_usd_m2"] = city_meta.get("market_rental_price_usd_m2")
        result["market_rental_yield_annual_pct"] = city_meta.get("market_rental_yield_annual_pct")

    return result


# ---------------------------------------------------------------------------
# 6. Orquestador principal
# ---------------------------------------------------------------------------

def run_full_analysis(req: ScoreRequest) -> dict:
    city = CITY_REFERENCE_DATA.get(req.city_key)
    if city is None:
        raise ValueError(f"Ciudad no reconocida: {req.city_key}")

    fiber_dist = req.fiber_backbone_distance_km or city["fiber_backbone_distance_km"]
    outage_hours = req.avg_outage_hours_month or city["avg_outage_hours_month"]
    grid_data_source = "override_usuario" if req.avg_outage_hours_month else city.get("grid_data_source", "estimado")

    sri = compute_signal_resilience_index(
        distance_to_tower_km=req.distance_to_tower_km,
        freq_mhz=req.frequency_mhz,
        fiber_backbone_distance_km=fiber_dist,
        tower_density_km2=city["tower_density_km2"],
    )

    eri = compute_energy_resilience_index(
        avg_outage_hours_month=outage_hours,
        solar_irradiance_kwh_m2_day=city["solar_irradiance_kwh_m2_day"],
        panel_area_m2=req.panel_area_m2,
        panel_efficiency=req.panel_efficiency,
        daily_consumption_kwh=req.daily_consumption_kwh,
    )
    eri["solar_data_source"] = city.get("solar_data_source", "estimado")
    if eri["solar_data_source"] == "real":
        eri["solar_data_source_detail"] = city.get("solar_data_source_detail")
        eri["explanation"] += " Irradiancia obtenida de climatología satelital real (NASA POWER)."
    eri["grid_data_source"] = grid_data_source
    if grid_data_source == "proxy_ovsp":
        eri["grid_severity_tier"] = city.get("grid_severity_tier")
        eri["grid_data_source_detail"] = city.get("grid_data_source_detail")
        eri["explanation"] += (
            f" Horas de corte basadas en índice de severidad eléctrica "
            f"({city.get('grid_severity_tier')}), derivado de encuestas OVSP y prensa citada, no medición directa."
        )

    composite = compute_composite_score(
        base_value_usd_m2=city["base_value_usd_m2"],
        property_area_m2=req.property_area_m2,
        property_type=req.property_type,
        sri_score=sri["score"],
        eri_score=eri["score"],
        city_meta=city,
    )

    return {
        "city": city["label"],
        "coordinates": {"lat": city["lat"], "lon": city["lon"]},
        "telecom": sri,
        "energy": eri,
        "real_estate": composite,
        "inputs_echo": {
            "distance_to_tower_km": req.distance_to_tower_km,
            "frequency_mhz": req.frequency_mhz,
            "fiber_backbone_distance_km": fiber_dist,
            "avg_outage_hours_month": outage_hours,
            "panel_area_m2": req.panel_area_m2,
            "panel_efficiency": req.panel_efficiency,
            "daily_consumption_kwh": req.daily_consumption_kwh,
            "property_area_m2": req.property_area_m2,
            "property_type": req.property_type,
        },
    }
