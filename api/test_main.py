"""Run: python test_main.py"""
import math
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
from PIL import Image  # noqa: E402

from main import Analysis, BenchPrint, Print, analyze, exposure  # noqa: E402

# Denser (darker) negative transmits less light -> lower log_mean.
thin, dense = analyze(Image.new("L", (100, 100), 200), 10), analyze(Image.new("L", (100, 100), 100), 10)
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

# Autocrop: a bright border is ignored once cropped away; 100% still measures the centre.
from PIL import ImageDraw  # noqa: E402

from main import density  # noqa: E402

framed = Image.new("L", (100, 100), 255)
ImageDraw.Draw(framed).rectangle((20, 20, 79, 79), fill=60)
assert density(framed, 0) > density(framed, 40) == density(Image.new("L", (10, 10), 60), 0)
assert density(framed, 100) == density(framed, 40)
assert c.get("/api/settings").json() == {"autocrop": 10, "contrast": 2.2}
assert c.put("/api/settings", json={"autocrop": 101}).status_code == 422
buf2 = BytesIO()
framed.convert("RGB").save(buf2, "JPEG")
c.put("/api/benchmark/image", files={"file": ("b.jpg", buf2.getvalue(), "image/jpeg")})
before = c.get("/api/benchmark").json()["analysis"]["log_mean"]
assert c.put("/api/settings", json={"autocrop": 50}).json()["autocrop"] == 50
assert c.get("/api/benchmark").json()["analysis"]["log_mean"] < before  # border cropped -> denser reading
print("settings ok")

# Pan: crop window slides to the bright or dark half; benchmark re-measured on pan change.
halves = Image.new("L", (100, 100), 255)
ImageDraw.Draw(halves).rectangle((50, 0, 99, 99), fill=20)
assert density(halves, 50, -1, 0) == density(Image.new("L", (10, 10), 255), 0)
assert density(halves, 50, 1, 0) == density(Image.new("L", (10, 10), 20), 0)
assert density(halves, 50, 0, 1) == density(halves, 50, 0, -1) == density(halves, 50)
buf3 = BytesIO()
halves.convert("RGB").save(buf3, "JPEG", quality=95)
c.put("/api/benchmark/image", files={"file": ("b.jpg", buf3.getvalue(), "image/jpeg")})
left = c.put("/api/benchmark/print", json={"pan_x": -1}).json()["analysis"]["log_mean"]
right = c.put("/api/benchmark/print", json={"pan_x": 1}).json()["analysis"]["log_mean"]
assert left > right + 3, (left, right)
assert c.put("/api/benchmark/print", json={"pan_x": 2}).status_code == 422
r = c.post("/api/analyze", files={"file": ("b.jpg", buf3.getvalue(), "image/jpeg")}, data={"pan_x": "1"})
assert abs(r.json()["log_mean"] - right) < 0.01
print("pan ok")
for crop in (0, 33, 99.5, 100):
    for px in (-1, 1):
        assert density(Image.new("L", (37, 23), 255), crop, px, px) == density(Image.new("L", (1, 1), 255), 0), (crop, px)
print("bounds ok")

# 3:2 box: on a square photo the box is 3:2 (or 2:3 upright / rotated); pan slides it along the free axis.
from main import box_aspect  # noqa: E402

assert box_aspect(0, False) == 1.5 and box_aspect(1, False) == box_aspect(0, True) == 2 / 3
sq = Image.new("L", (90, 90), 255)
ImageDraw.Draw(sq).rectangle((0, 0, 89, 24), fill=20)  # dark strip along the top
assert density(sq, 0, 0, -1, 1.5) < density(sq, 0, 0, 0, 1.5) < density(sq, 0, 0, 1, 1.5)  # box moves down past it
assert density(sq, 0, 0, 1, 1.5) == density(Image.new("L", (1, 1), 255), 0)
assert density(sq, 0, -1, 0, 2 / 3) == density(sq, 0, 1, 0, 2 / 3)  # upright box spans full height, x-pan irrelevant
r = c.post("/api/analyze", files={"file": ("b.jpg", buf3.getvalue(), "image/jpeg")}, data={"rotation": "1", "portrait": "true"})
assert r.status_code == 200, r.text
print("box ok")

# Shrunk uploads carry no EXIF: the browser sends the phone settings as form fields instead.
r = c.post("/api/analyze", files={"file": ("b.jpg", buf3.getvalue(), "image/jpeg")}, data={"aperture": "2.2", "shutter": "0.008333", "iso": "100"}).json()
assert r["iso"] == 100 and abs(r["ev"] - 9.18) < 0.01, r
assert c.post("/api/analyze", files={"file": ("b.jpg", buf3.getvalue(), "image/jpeg")}, data={"iso": "-5"}).status_code == 422
print("camera ok")

# Originals are turned upright (EXIF orientation) before measuring, keeping their phone settings.
side = Image.new("L", (60, 40), 255)
ImageDraw.Draw(side).rectangle((0, 0, 29, 39), fill=10)  # dark left half
ex = Image.Exif()
ex[0x0112] = 6  # stored sideways: display = rotate 90° clockwise, so the dark half ends up on top
ex.get_ifd(0x8769).update({33437: 2.2, 33434: 0.01, 34855: 100})
buf4 = BytesIO()
side.convert("RGB").save(buf4, "JPEG", quality=95, exif=ex.tobytes())
r = c.post("/api/analyze", files={"file": ("s.jpg", buf4.getvalue(), "image/jpeg")}, data={"pan_y": "-1", "rotation": "1", "keep": "true"}).json()
assert r["iso"] == 100, r
top = r["log_mean"]
r = c.post("/api/analyze", files={"file": ("s.jpg", buf4.getvalue(), "image/jpeg")}, data={"pan_y": "1", "rotation": "1"}).json()
assert top < r["log_mean"] - 3, (top, r)
assert Image.open(BytesIO(c.get("/api/negative/image").content)).size == (40, 60)
print("upright ok")
