import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.errors import ApiError
from app.services import blister_counting


def _settings(**overrides):
    values = {
        "vision_ai_api_key": "test-key",
        "vision_ai_base_url": "https://vision.example/v1",
        "vision_ai_model": "test-vision-model",
        "vision_ai_timeout_seconds": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_count_blister_requires_server_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(blister_counting, "get_settings", lambda: _settings(vision_ai_api_key=""))

    with pytest.raises(ApiError) as error:
        blister_counting.count_blister_tablets(str(tmp_path / "blister.jpg"), "image/jpeg")

    assert error.value.status_code == 503


def test_count_blister_rejects_missing_image(monkeypatch, tmp_path):
    monkeypatch.setattr(blister_counting, "get_settings", lambda: _settings())

    with pytest.raises(ApiError) as error:
        blister_counting.count_blister_tablets(str(tmp_path / "missing.jpg"), "image/jpeg")

    assert error.value.status_code == 410


def test_count_blister_returns_proposal_without_stock_side_effect(monkeypatch, tmp_path):
    image = tmp_path / "blister.jpg"
    image.write_bytes(b"test-image")
    monkeypatch.setattr(blister_counting, "get_settings", lambda: _settings())

    result_json = {
        "visible_tablet_count": 9,
        "confidence": 0.94,
        "medicine_text": "Example 500mg",
        "uncertain": False,
        "notes": "Nine visible tablets",
    }
    response_payload = {
        "choices": [{"message": {"content": json.dumps(result_json)}}]
    }
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(response_payload).encode("utf-8")
    urlopen = Mock(return_value=response)
    monkeypatch.setattr(blister_counting.urllib.request, "urlopen", urlopen)

    result = blister_counting.count_blister_tablets(str(image), "image/jpeg")

    assert result["visible_tablet_count"] == 9
    assert result["confidence"] == 0.94
    assert result["medicine_text"] == "Example 500mg"
    assert result["requires_human_confirmation"] is True
    urlopen.assert_called_once()
