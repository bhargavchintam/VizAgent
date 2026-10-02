import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


@pytest.fixture
def client(monkeypatch):
    for var in ("VSS_URL", "INGRESS_URL", "VSS_USERNAME", "USERNAME", "VSS_PASSWORD", "PASSWORD", "WANDB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import importlib

    import main

    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def test_health_reports_unconfigured(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "vss_configured": False, "llm_configured": False, "tracing": False}


def test_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "VizAgent" in resp.text


def test_search_without_vss_is_503(client):
    resp = client.post("/api/search", json={"query": "person near a car"})
    assert resp.status_code == 503


def test_chat_json_strips_fences(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "chat", lambda *a, **k: '```json\n{"match": true, "severity": 4}\n```')
    assert llm.chat_json([]) == {"match": True, "severity": 4}
