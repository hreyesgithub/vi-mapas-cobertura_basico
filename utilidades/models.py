"""
models.py
=========
Esquemas Pydantic para validación de entrada/salida de la API.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class ScoreRequestModel(BaseModel):
    # No se restringe con Literal: las claves válidas dependen de
    # CITY_REFERENCE_DATA (estimadas + zonas reales cargadas desde
    # data/market_reference.json), que puede crecer sin tocar este modelo.
    # scoring_engine.run_full_analysis valida la clave y responde 400 si no existe.
    city_key: str = Field(..., description="Clave de ciudad/zona de referencia (ver GET /api/cities)")

    distance_to_tower_km: float = Field(
        1.0, ge=0.01, le=50, description="Distancia a la torre celular más cercana (km)"
    )
    frequency_mhz: float = Field(
        1900.0, ge=700, le=3800, description="Banda de frecuencia usada (MHz)"
    )
    fiber_backbone_distance_km: Optional[float] = Field(
        None, ge=0, le=100, description="Distancia al backbone de fibra óptica (km). Si se omite, se usa el dato de referencia de la ciudad."
    )
    avg_outage_hours_month: Optional[float] = Field(
        None, ge=0, le=300, description="Horas de corte eléctrico promedio al mes. Si se omite, se usa el dato de referencia de la ciudad."
    )
    panel_area_m2: float = Field(20.0, ge=1, le=500, description="Área disponible para paneles solares (m²)")
    panel_efficiency: float = Field(0.20, ge=0.10, le=0.30, description="Eficiencia del panel solar (0-1)")
    daily_consumption_kwh: float = Field(15.0, ge=1, le=200, description="Consumo eléctrico diario estimado (kWh)")
    property_area_m2: float = Field(120.0, ge=10, le=5000, description="Área del inmueble (m²)")
    property_type: Literal["residencial", "comercial", "industrial"] = Field("residencial")


class CityInfo(BaseModel):
    key: str
    label: str
    lat: float
    lon: float
    base_value_usd_m2: float
    data_source: str = "estimado"
    market_sample_size: Optional[int] = None


class SavedScanRequest(BaseModel):
    """Payload opcional para persistir un análisis en Supabase."""
    city_key: str
    vis_score: float
    payload: dict
