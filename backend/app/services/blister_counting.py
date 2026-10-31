"""Image-based blister tablet counting through a configurable vision AI provider.

The model returns a proposal only. This module never writes inventory.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from app.config import get_settings
from app.errors import ApiError


def count_blister_tablets(image_path: str, content_type: str) -> dict:
    settings = get_settings()
    if not settings.vision_ai_api_key:
        raise ApiError(503, "Vision AI is not configured. Set VISION_AI_API_KEY on the server.", "vision_ai_not_configured")
    path = Path(image_path)
    if not path.is_file():
        raise ApiError(410, "The captured image is no longer available. Capture it again.", "capture_image_unavailable")

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    prompt = (
        "Analyze this photograph of a medicine blister pack. Count only individual tablets/capsules "
        "that are visibly present in their pockets. Do not count empty pockets, printed graphics, "
        "or infer hidden tablets. If glare, overlap, blur, foil, or occlusion prevents a reliable "
        "count, lower confidence and explain. Identify visible medicine name/strength only if legible. "
        "Return ONLY JSON with keys: visible_tablet_count (integer or null), confidence (number 0..1), "
        "medicine_text (string or null), uncertain (boolean), notes (short string). "
        "A count is a visual estimate, not a stock transaction."
    )
    body = {
        "model": settings.vision_ai_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a cautious pharmacy blister-image counting assistant. Never guess obscured tablets."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{encoded}"}},
            ]},
        ],
    }
    request = urllib.request.Request(
        settings.vision_ai_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.vision_ai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.vision_ai_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = payload["choices"][0]["message"]["content"]
        result = json.loads(raw)
        count = result.get("visible_tablet_count")
        confidence = float(result.get("confidence", 0))
        if count is not None and (isinstance(count, bool) or int(count) != count or int(count) < 0):
            raise ValueError("invalid count")
        if not 0 <= confidence <= 1:
            raise ValueError("invalid confidence")
        return {
            "visible_tablet_count": int(count) if count is not None else None,
            "confidence": confidence,
            "medicine_text": result.get("medicine_text"),
            "uncertain": bool(result.get("uncertain", True)),
            "notes": str(result.get("notes") or "")[:500],
            "provider_model": settings.vision_ai_model,
            "requires_human_confirmation": True,
        }
    except urllib.error.HTTPError as exc:
        raise ApiError(502, f"Vision AI provider returned HTTP {exc.code}", "vision_ai_provider_error") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ApiError(503, "Vision AI service is temporarily unavailable", "vision_ai_unavailable") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError(502, "Vision AI returned an invalid counting response", "vision_ai_invalid_response") from exc
