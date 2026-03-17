from __future__ import annotations

import os

DEFAULT_API_BASE_URL = "http://backend:8000"


def get_api_base_url() -> str:
    raw = os.getenv("API_BASE_URL")
    return raw.strip() if raw and raw.strip() else DEFAULT_API_BASE_URL
