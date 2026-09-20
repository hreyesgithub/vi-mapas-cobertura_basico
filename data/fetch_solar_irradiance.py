"""
fetch_solar_irradiance.py
==========================
Reemplaza el valor ESTIMADO de irradiancia solar (`solar_irradiance_kwh_m2_day`)
por un dato REAL de origen satelital, usando la API pública y gratuita de
NASA POWER (Prediction Of Worldwide Energy Resources):

    https://power.larc.nasa.gov/

Se usa el endpoint de "climatology" (climatología de largo plazo, ~20 años de
histórico satelital: 2001-2020), parámetro `ALLSKY_SFC_SW_DWN` (irradiancia
global horizontal, All-Sky Surface Shortwave Downward), que la API ya entrega
en kWh/m²/día — exactamente la unidad que usa `scoring_engine.py` en su
fórmula de generación fotovoltaica. No requiere API key ni registro.

Por qué un paso de ingesta y no una llamada en vivo en cada request de la API:
  - No depende de que power.larc.nasa.gov esté disponible en el momento en
    que alguien usa el dashboard (evita que una caída externa tumbe /api/score).
  - La climatología de un punto geográfico no cambia de un día para otro —
    tiene sentido cachearla y refrescarla ocasionalmente, no por request.
  - Mismo patrón que `ingest_market_data.py`: fuente externa -> JSON
    agregado y versionado -> `scoring_engine.py` lo carga en runtime.

Uso:
    python3 fetch_solar_irradiance.py
    # Genera/actualiza: data/solar_irradiance.json

Requiere acceso saliente a internet hacia power.larc.nasa.gov (no disponible
en entornos con egress restringido — ver nota en el README). Al desplegar en
Render, o al correrlo en una máquina local con internet normal, funciona sin
configuración adicional.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Import local: reutiliza las coordenadas ya definidas en scoring_engine.py
# (ciudades estimadas + zonas reales cargadas desde market_reference.json),
# para no duplicar una lista de coordenadas en dos archivos distintos.
sys.path.insert(0, str(Path(__file__).parent.parent))
from scoring_engine import CITY_REFERENCE_DATA  # noqa: E402

OUTPUT_PATH = Path(__file__).parent / "solar_irradiance.json"

NASA_POWER_BASE_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
PARAMETER = "ALLSKY_SFC_SW_DWN"
COMMUNITY = "RE"  # "Renewable Energy" — parámetros orientados a energía solar/eólica
REQUEST_TIMEOUT_SECONDS = 20
SLEEP_BETWEEN_REQUESTS_SECONDS = 1.0  # cortesía con la API pública, evita bloqueo


def fetch_climatology_irradiance(lat: float, lon: float) -> float:
    """Consulta NASA POWER y retorna la irradiancia promedio ANUAL (ANN) en
    kWh/m²/día para el punto (lat, lon), calculada sobre climatología de
    largo plazo (~20 años)."""
    url = (
        f"{NASA_POWER_BASE_URL}"
        f"?parameters={PARAMETER}&community={COMMUNITY}"
        f"&longitude={lon}&latitude={lat}&format=JSON"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "venezuela-insights-mvp/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    # Estructura de respuesta de NASA POWER:
    # { "properties": { "parameter": { "ALLSKY_SFC_SW_DWN": {"JAN": .., ..., "ANN": .. } } } }
    monthly = payload["properties"]["parameter"][PARAMETER]
    if "ANN" not in monthly:
        raise ValueError(f"Respuesta sin promedio anual (ANN): {monthly}")
    return float(monthly["ANN"])


def main():
    results = {}
    errors = {}

    print(f"Consultando NASA POWER para {len(CITY_REFERENCE_DATA)} ciudades/zonas...\n")

    for key, city in CITY_REFERENCE_DATA.items():
        lat, lon = city["lat"], city["lon"]
        try:
            irradiance = fetch_climatology_irradiance(lat, lon)
            results[key] = {
                "label": city["label"],
                "lat": lat,
                "lon": lon,
                "solar_irradiance_kwh_m2_day": round(irradiance, 3),
                "source": "NASA POWER — climatología ANN, ALLSKY_SFC_SW_DWN (~2001-2020)",
            }
            print(f"  OK  {key:20s} {irradiance:.2f} kWh/m²/día")
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as e:
            errors[key] = str(e)
            print(f"  ERR {key:20s} {e}")

        time.sleep(SLEEP_BETWEEN_REQUESTS_SECONDS)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "NASA POWER (power.larc.nasa.gov), endpoint climatology/point",
        "parameter": PARAMETER,
        "community": COMMUNITY,
        "cities": results,
        "errors": errors,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nOK: {len(results)} zonas con dato real, {len(errors)} con error -> {OUTPUT_PATH}")
    if errors:
        print("Las zonas con error conservan su valor ESTIMADO en scoring_engine.py.")


if __name__ == "__main__":
    main()
