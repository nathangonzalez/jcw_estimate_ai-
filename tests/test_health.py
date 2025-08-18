import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app import app


def test_health_endpoint():
    client = app.test_client()
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}
