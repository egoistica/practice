from __future__ import annotations

import uuid

from fastapi import Request


def extract_lecture_id_from_path(path: str) -> uuid.UUID | None:
    # Expected protected routes:
    # /lectures/{lecture_id}
    # /lectures/{lecture_id}/summary
    # /lectures/{lecture_id}/graph
    # /lectures/{lecture_id}/export
    # ...and other lecture sub-routes with the same prefix.
    if not path.startswith("/lectures/"):
        return None

    remainder = path[len("/lectures/") :]
    if not remainder:
        return None

    raw_id = remainder.split("/", 1)[0].strip()
    if not raw_id:
        return None

    try:
        return uuid.UUID(raw_id)
    except (ValueError, TypeError):
        return None


def extract_bearer_token(request: Request) -> str | None:
    raw = request.headers.get("authorization")
    if not raw:
        return None
    token = raw.strip()
    if not token:
        return None
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None
