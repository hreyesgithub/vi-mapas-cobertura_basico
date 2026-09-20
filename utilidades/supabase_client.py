"""
supabase_client.py
===================
Capa de persistencia OPCIONAL sobre Supabase.

Si las variables de entorno SUPABASE_URL y SUPABASE_KEY no están definidas,
el cliente queda deshabilitado y la API sigue funcionando en modo "stateless"
(sin guardar historial). Esto permite correr el MVP sin depender de Supabase.

Tabla esperada en Supabase (crear manualmente vía SQL editor):

    create table scans (
        id uuid primary key default gen_random_uuid(),
        created_at timestamp with time zone default now(),
        city_key text not null,
        vis_score numeric not null,
        payload jsonb not null
    );
"""

import os
from typing import Any, Optional

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

_client: Any = None
_enabled = bool(SUPABASE_URL and SUPABASE_KEY)

if _enabled:
    try:
        from supabase.client import create_client
        # _enabled garantiza que ambas variables estén definidas, pero el
        # analizador estático no puede inferirlo a partir de esa expresión.
        assert SUPABASE_URL is not None and SUPABASE_KEY is not None
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        # El paquete supabase-py no está instalado; se degrada a modo deshabilitado.
        _enabled = False
        _client = None


def is_enabled() -> bool:
    return _enabled and _client is not None


def save_scan(city_key: str, vis_score: float, payload: dict) -> Optional[dict]:
    """Guarda un escaneo en Supabase. Retorna None silenciosamente si está deshabilitado."""
    if not is_enabled():
        return None
    result = _client.table("scans").insert({
        "city_key": city_key,
        "vis_score": vis_score,
        "payload": payload,
    }).execute()
    return result.data


def get_recent_scans(limit: int = 20):
    if not is_enabled():
        return []
    result = (
        _client.table("scans")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
