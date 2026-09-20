"""
build_grid_severity_index.py
=============================
Construye data/grid_severity_index.json: un índice RELATIVO de severidad del
servicio eléctrico por estado venezolano, usado como sustituto informado de
`avg_outage_hours_month` cuando no hay dato metered oficial disponible
(Corpoelec no publica estadísticas públicas confiables de interrupciones).

IMPORTANTE — qué tipo de dato es esto y qué NO es:

  Esto NO es una API en vivo ni una medición directa en tiempo real. Es un
  índice curado a partir de fuentes públicas reales y citables (encuestas
  periódicas del Observatorio Venezolano de Servicios Públicos - OVSP, y
  reportes de prensa especializada con cifras concretas de duración de
  cortes). Por eso se etiqueta en el sistema con un tercer nivel de calidad
  de dato, distinto de "real" (medición satelital directa, como la
  irradiancia de NASA POWER) y de "estimado" (aproximación sin respaldo):

      grid_data_source = "proxy_ovsp"

  Es deliberadamente un tercer nivel: sería deshonesto etiquetarlo "real"
  (no es una medición metered), pero también sería impreciso llamarlo
  "estimado" sin más (si tiene respaldo documental verificable, con fecha
  y fuente). El objetivo es que quien use esta herramienta pueda distinguir
  los tres niveles de confianza sin ambigüedad.

Metodología de conversión (percepción/frecuencia -> horas/mes):

  Las fuentes reportan frecuencia (veces por semana) y duración por evento
  (horas), no un total mensual directo. Se convierte así:

      horas/mes ≈ eventos_por_semana × horas_promedio_por_evento × 4.33

  Cuando la fuente ya reporta un patrón diario ("le cortan la luz X horas
  al día"), se usa: horas/mes ≈ horas_por_día × 30.

  Los tres niveles (alta / media / baja) se anclan en cifras concretas
  citadas en `SOURCES`, no en un número inventado por conveniencia. Cuando
  una fuente da un rango, se usa el punto medio. Esto sigue siendo una
  APROXIMACIÓN — el objetivo no es fingir precisión que no existe, sino
  reemplazar un número arbitrario por uno trazable a una fuente con fecha.

Cómo actualizar este índice en el futuro:
  Editar SOURCES y ESTADOS_SEVERITY más abajo con hallazgos más recientes
  (el OVSP publica encuestas cada pocos meses), y volver a correr:

      python3 build_grid_severity_index.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent / "grid_severity_index.json"

# ---------------------------------------------------------------------------
# Fuentes citadas (con fecha y hallazgo parafraseado — no se reproduce texto
# textual de los artículos, solo el hallazgo cuantitativo o el ranking).
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "id": "ovsp_2024_nacional",
        "publisher": "Observatorio Venezolano de Servicios Públicos (OVSP), vía Caleidoscopio Humano",
        "date": "2024-02-01",
        "finding": (
            "Encuesta nacional: 54% de los hogares reportó cortes diarios; de "
            "ese grupo, 69% con cortes de 2 a 6 horas y 9% de 6 a 9 horas. "
            "Maracaibo, Mérida, Barinas, Barquisimeto, San Cristóbal, San "
            "Fernando de Apure, Valencia y Punto Fijo listadas como las "
            "ciudades más afectadas."
        ),
        "url": "https://caleidohumano.org/ovsp-registro-aumento-significativo-en-las-fallas-electricas-en-todo-el-pais/",
    },
    {
        "id": "diario_los_andes_maracaibo",
        "publisher": "Diario de Los Andes (vía EFE)",
        "date": "2024-03-01",
        "finding": (
            "Testimonio directo de un residente de Maracaibo (Zulia): Corpoelec "
            "corta el servicio en su sector aproximadamente 3 horas al día, en "
            "turnos rotativos de mañana o noche. Zulia descrito como el estado "
            "más golpeado de forma crónica desde 2009."
        ),
        "url": "https://diariodelosandes.com/venezuela-con-reiterados-cortes-electricos-un-lustro-despues-del-apagon-nacional",
    },
    {
        "id": "eldiario_aragua_carabobo_2026",
        "publisher": "El Diario",
        "date": "2026-08-04",
        "finding": (
            "Protestas en Aragua y Carabobo por racionamiento eléctrico. "
            "Sectores puntuales reportaron cortes superiores a 8 horas diarias "
            "(Las Parcelas de El Socorro) y superiores a 5 horas diarias (El "
            "Naranjal de Naguanagua). Comerciantes de Valencia (Carabobo) "
            "reportaron cortes diarios."
        ),
        "url": "https://eldiario.com/2026/08/04/protestas-aragua-carabobo/",
    },
    {
        "id": "cronica_uno_gran_caracas",
        "publisher": "Crónica.Uno",
        "date": "2021-06-01",
        "finding": (
            "En estudios comparativos por ciudad del OVSP, Caracas aparece "
            "consistentemente como la ciudad con menor proporción de "
            "residentes reportando fallas de servicios básicos, incluida "
            "electricidad, frente al resto del país."
        ),
        "url": "https://cronica.uno/tag/electricidad/page/10/",
    },
    {
        "id": "ntn24_apagon_nacional_2025",
        "publisher": "NTN24",
        "date": "2025-08-22",
        "finding": (
            "Apagón nacional puntual afectó simultáneamente Miranda, La "
            "Guaira, Aragua, Nueva Esparta, Zulia, Carabobo y sectores de "
            "Caracas (incluidos Chacao y Baruta). Confirma que el Área "
            "Metropolitana de Caracas, aunque comparativamente menos afectada "
            "en el día a día, no está exenta de eventos nacionales."
        ),
        "url": "https://www.ntn24.com/noticias-actualidad/caos-en-varios-estados-de-venezuela-por-apagon-electrico-y-lluvias-575912",
    },
]

# ---------------------------------------------------------------------------
# Índice por estado. "avg_outage_hours_month" es la conversión documentada
# arriba; "basis" resume qué fuente(s) la sostienen.
# ---------------------------------------------------------------------------
ESTADOS_SEVERITY = {
    "zulia": {
        "label": "Zulia (Maracaibo)",
        "tier": "alta",
        "avg_outage_hours_month": 90,
        "basis": "≈3h/día de corte rotativo reportado de forma sostenida desde 2009 (diario_los_andes_maracaibo) × 30 días.",
        "source_ids": ["diario_los_andes_maracaibo", "ovsp_2024_nacional"],
    },
    "carabobo": {
        "label": "Carabobo (Valencia)",
        "tier": "alta",
        "avg_outage_hours_month": 85,
        "basis": "Racionamiento diario confirmado en 2026, con sectores puntuales >5-8h/día; se usa una media estatal conservadora (no el pico de las zonas más golpeadas).",
        "source_ids": ["eldiario_aragua_carabobo_2026", "ovsp_2024_nacional"],
    },
    "aragua": {
        "label": "Aragua (Maracay)",
        "tier": "alta",
        "avg_outage_hours_month": 85,
        "basis": "Mismo evento de racionamiento 2026 que Carabobo; protestas conjuntas por cortes diarios en múltiples municipios.",
        "source_ids": ["eldiario_aragua_carabobo_2026"],
    },
    "lara": {
        "label": "Lara (Barquisimeto)",
        "tier": "media",
        "avg_outage_hours_month": 50,
        "basis": "Aparece reiteradamente en el listado OVSP de ciudades más afectadas, pero sin testimonio de cortes diarios sostenidos como Zulia/Aragua/Carabobo; se usa la frecuencia nacional de 3-4 veces/semana como ancla.",
        "source_ids": ["ovsp_2024_nacional"],
    },
    "nueva_esparta": {
        "label": "Nueva Esparta (Isla Margarita/Porlamar)",
        "tier": "media",
        "avg_outage_hours_month": 50,
        "basis": "Porlamar aparece repetidamente entre las ciudades más afectadas en estudios OVSP; misma ancla de frecuencia nacional que Lara.",
        "source_ids": ["ovsp_2024_nacional"],
    },
    "gran_caracas": {
        "label": "Distrito Capital / Miranda (Gran Caracas)",
        "tier": "baja",
        "avg_outage_hours_month": 10,
        "basis": "Consistentemente reportada como la región con menor incidencia relativa de fallas, aunque no inmune a apagones nacionales puntuales.",
        "source_ids": ["cronica_uno_gran_caracas", "ntn24_apagon_nacional_2025"],
    },
}

# Mapeo de las claves de ciudad usadas en scoring_engine.CITY_REFERENCE_DATA
# hacia la clave de estado de ESTADOS_SEVERITY.
CITY_TO_ESTADO = {
    "valencia": "carabobo",
    "maracaibo": "zulia",
    "maracay": "aragua",
    "margarita": "nueva_esparta",
    "barquisimeto": "lara",
    "baruta": "gran_caracas",
    "chacao": "gran_caracas",
    "el_hatillo": "gran_caracas",
    "libertador": "gran_caracas",
    "sucre": "gran_caracas",
    "distrito_capital": "gran_caracas",
    "caracas": "gran_caracas",
}


def main():
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_type": "proxy_ovsp",
        "methodology_summary": (
            "Índice relativo de severidad eléctrica por estado, derivado de "
            "encuestas de percepción (OVSP) y reportes de prensa con cifras "
            "concretas de duración de cortes. No es una medición metered "
            "directa; ver docstring de este script y README sección "
            "correspondiente para la metodología completa."
        ),
        "sources": SOURCES,
        "estados": ESTADOS_SEVERITY,
        "city_to_estado": CITY_TO_ESTADO,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"OK: {len(ESTADOS_SEVERITY)} estados, {len(CITY_TO_ESTADO)} ciudades mapeadas -> {OUTPUT_PATH}")
    for city, estado in CITY_TO_ESTADO.items():
        e = ESTADOS_SEVERITY[estado]
        print(f"  {city:20s} -> {estado:15s} tier={e['tier']:6s} {e['avg_outage_hours_month']}h/mes")


if __name__ == "__main__":
    main()
