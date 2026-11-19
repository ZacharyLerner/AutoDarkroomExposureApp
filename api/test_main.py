"""Run: python test_main.py"""
import math
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
from PIL import Image  # noqa: E402

from main import Analysis, BenchPrint, Print, analyze, exposure  # noqa: E402

# Denser (darker) negative transmits less light -> lower log_mean.
thin, dense = analyze(Image.new("L", (100, 100), 200)), analyze(Image.new("L", (100, 100), 100))
assert dense.log_mean < thin.log_mean and thin.ev is None

bp = BenchPrint(time=10, height=8, aperture=8)
same = exposure(thin, bp, thin, Print(height=8, aperture=8))
assert math.isclose(same["seconds"], 10), same

# One stop denser (+1), double height (+2), f/8 -> f/11.3 (+1), grade 4 filter (+1)
b = Analysis(log_mean=-2)
r = exposure(b, bp, Analysis(log_mean=-3), Print(height=16, aperture=8 * math.sqrt(2), grade=4))
assert math.isclose(r["seconds"], 10 * 2 ** (1 + 2 + 1 + 1)), r

# Brightening the new preview by 1 stop halves the time.
r = exposure(b, bp, b, Print(height=8, aperture=8, brightness=1))
assert math.isclose(r["seconds"], 5), r
print("ok")

# Prints: create, list, edit, image, delete; bad ids rejected.
from io import BytesIO  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

c = TestClient(app)
buf = BytesIO()
Image.new("RGB", (64, 48), (90, 90, 90)).save(buf, "JPEG")
r = c.post("/api/prints", files={"file": ("n.jpg", buf.getvalue(), "image/jpeg")}, data={"data": '{"time": 14.2, "height": 8, "aperture": 8}'})
assert r.status_code == 201, r.text
pid = r.json()["id"]
assert [p["id"] for p in c.get("/api/prints").json()] == [pid]
r = c.put(f"/api/prints/{pid}", json={"time": 16, "height": 8, "aperture": 11, "rotation": 1, "notes": "warmer paper"})
assert r.json()["print"]["rotation"] == 1 and r.json()["print"]["notes"] == "warmer paper" and r.json()["print"]["aperture"] == 11
assert c.get(f"/api/prints/{pid}/image").headers["content-type"] == "image/jpeg"
assert c.post("/api/prints", files={"file": ("n.jpg", b"x", "image/jpeg")}, data={"data": '{"time": -1}'}).status_code == 422
assert c.get("/api/prints/..%2Fbenchmark/image").status_code in (404, 422)
assert c.delete(f"/api/prints/{pid}").status_code == 204 and c.get("/api/prints").json() == []
print("prints ok")
