"""
ingest_market_data.py
======================
Convierte el Excel crudo de listings (scrapeado de Mercado Libre Venezuela,
hojas "Ventas" y "Alquileres") en un JSON agregado por zona/municipio que
scoring_engine.py consume en runtime.

Por qué un paso de ingesta separado y no leer el .xlsx directamente en la API:
  - El archivo real tiene 500+ filas (y seguirá creciendo); la API no debe
    cargar/parsear openpyxl en cada arranque ni depender de ese archivo
    estando presente en el deploy.
  - Permite aplicar limpieza (outliers, duplicados, tamaño de muestra mínimo)
    una sola vez y versionar el resultado agregado (`market_reference.json`)
    junto al código.
  - Cuando llegue el archivo completo (500+ registros), basta con volver a
    correr este script y commitear el JSON actualizado.

Uso:
    python3 ingest_market_data.py /ruta/al/archivo.xlsx
    # Genera/actualiza: data/market_reference.json

Reglas de limpieza aplicadas:
  - Se descartan filas marcadas outlier=True o es_duplicado=True.
  - Solo se agregan zonas/municipios con al menos MIN_SAMPLE_SIZE listings
    válidos en Ventas (las que tienen menos muestra quedan fuera para no
    reportar un precio "de mercado" poco confiable).
  - La mediana (no el promedio) se usa como estadístico central: es robusta
    ante outliers residuales típicos de datos scrapeados (precios digitados
    mal, listings de lujo atípicos, etc.).
"""

import sys
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

MIN_SAMPLE_SIZE = 5
OUTPUT_PATH = Path(__file__).parent / "market_reference.json"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text


def clean_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "outlier" in df.columns:
        df = df[df["outlier"] == False]  # noqa: E712
    if "es_duplicado" in df.columns:
        df = df[df["es_duplicado"] == False]  # noqa: E712
    df = df[df["precio_por_m2"].notna() & (df["precio_por_m2"] > 0)]
    df = df[df["ciudad"].notna() & (df["ciudad"].str.strip() != "")]
    return df


def aggregate_by_zone(ventas: pd.DataFrame, alquileres: pd.DataFrame) -> dict:
    zonas = {}
    ventas_grp = ventas.groupby("ciudad")["precio_por_m2"]
    alq_grp = alquileres.groupby("ciudad")["precio_por_m2"]

    all_cities = set(ventas["ciudad"].unique()) | set(alquileres["ciudad"].unique())

    for ciudad in sorted(all_cities):
        v = ventas_grp.get_group(ciudad) if ciudad in ventas_grp.groups else pd.Series(dtype=float)
        a = alq_grp.get_group(ciudad) if ciudad in alq_grp.groups else pd.Series(dtype=float)

        if len(v) < MIN_SAMPLE_SIZE:
            continue  # muestra insuficiente para reportar un precio de venta confiable

        venta_mediana = float(v.median())
        entry = {
            "label": ciudad,
            "ventas_precio_m2_mediana": round(venta_mediana, 2),
            "ventas_precio_m2_p25": round(float(v.quantile(0.25)), 2),
            "ventas_precio_m2_p75": round(float(v.quantile(0.75)), 2),
            "ventas_muestra": int(len(v)),
            "alquiler_precio_m2_mediana": round(float(a.median()), 2) if len(a) >= 3 else None,
            "alquiler_muestra": int(len(a)),
        }

        # Rentabilidad anual estimada (cap rate) = (alquiler_mensual_m2 * 12) / precio_venta_m2
        if entry["alquiler_precio_m2_mediana"]:
            entry["rental_yield_anual_pct"] = round(
                (entry["alquiler_precio_m2_mediana"] * 12 / venta_mediana) * 100, 2
            )
        else:
            entry["rental_yield_anual_pct"] = None

        zonas[slugify(ciudad)] = entry

    return zonas


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 ingest_market_data.py <archivo.xlsx>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Archivo no encontrado: {input_path}")
        sys.exit(1)

    xls = pd.ExcelFile(input_path)
    ventas = clean_sheet(pd.read_excel(xls, "Ventas"))
    alquileres = clean_sheet(pd.read_excel(xls, "Alquileres"))

    zonas = aggregate_by_zone(ventas, alquileres)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": input_path.name,
        "min_sample_size": MIN_SAMPLE_SIZE,
        "total_listings_ventas": int(len(ventas)),
        "total_listings_alquileres": int(len(alquileres)),
        "zonas": zonas,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {len(zonas)} zonas agregadas -> {OUTPUT_PATH}")
    for key, z in zonas.items():
        print(f"  {key:25s} n_venta={z['ventas_muestra']:<4} mediana_venta_m2=${z['ventas_precio_m2_mediana']}")


if __name__ == "__main__":
    main()
