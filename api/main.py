import json
import math
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

DATA = Path(os.environ.get("DATA_DIR", "data"))
DATA.mkdir(parents=True, exist_ok=True)
RECORD = DATA / "benchmark.json"
IMAGE = DATA / "benchmark.jpg"
PRINTS = DATA / "prints"
SETTINGS = DATA / "settings.json"
PRINTS.mkdir(exist_ok=True)
PrintId = Annotated[str, PathParam(pattern=r"^[0-9a-f]{32}$")]  # also blocks path traversal
NEGATIVE = DATA / "negative.jpg"  # small copy of the new negative being worked on (one slot)
MAX_UPLOAD = 150_000_000  # originals go up whole: Safari applies the HDR gain map when decoding, which skews tones

# sRGB 0-255 -> linear light
LIN = [(v / 12.92) if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in (i / 255 for i in range(256))]


class Analysis(BaseModel):
    aperture: float | None = None
    shutter: float | None = None
    iso: float | None = None
    ev: float | None = None
    log_mean: float  # log2 of mean light transmitted through the negative


class Print(BaseModel):
    height: float = Field(gt=0)
    aperture: float = Field(gt=0)
    grade: float = Field(2.5, ge=0, le=5)
    brightness: float = Field(0, ge=-3, le=3)  # stops the preview was brightened to match the print


class BenchPrint(Print):
    time: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)
    aperture: float = Field(8, gt=0)
    rotation: int = Field(0, ge=0, le=3)  # preview only: quarter turns counterclockwise
    pan_x: float = Field(0, ge=-1, le=1)  # crop window position: -1 = left/top edge, 1 = right/bottom
    pan_y: float = Field(0, ge=-1, le=1)
    portrait: bool = False  # 3:2 crop box turned upright (2:3)


class SavedPrint(BenchPrint):
    notes: str = Field("", max_length=2000)


class Settings(BaseModel):
    autocrop: float = Field(10, ge=0, le=100)  # % of width/height trimmed, split evenly between opposite edges
    contrast: float = Field(2.2, ge=1, le=4)  # preview only: undoes the phone's tonal compression of the negative


class ExposureIn(BaseModel):
    analysis: Analysis
    print: Print


def open_image(raw: bytes) -> Image.Image:
    try:
        im = Image.open(BytesIO(raw))
        im.load()
        return im
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "That file couldn't be opened as an image.")


def box_aspect(rotation: int, portrait: bool) -> float:
    """The 3:2 crop box's width/height in the photo's own (unrotated) axes."""
    a = 2 / 3 if portrait else 3 / 2
    return 1 / a if rotation % 2 else a


def density(im: Image.Image, crop: float, pan_x: float = 0, pan_y: float = 0, aspect: float | None = None) -> float:
    """log2 of mean light through the negative inside the crop box (aspect None = the photo's own shape)."""
    g = im.convert("L")
    g.thumbnail((400, 400))
    w, h = g.size
    aspect = aspect or w / h
    fw, fh = ((h * aspect) / w, 1.0) if w / h > aspect else (1.0, w / (aspect * h))  # largest box that fits
    f = max(1 - crop / 100, 0.01)  # autocrop zooms in on the box
    cw, ch = max(1, round(w * fw * f)), max(1, round(h * fh * f))
    x0 = min(w - cw, max(0, round((w - cw) / 2 * (1 + pan_x))))  # stay inside: Pillow pads black
    y0 = min(h - ch, max(0, round((h - ch) / 2 * (1 + pan_y))))
    g = g.crop((x0, y0, x0 + cw, y0 + ch))
    hist = g.histogram()
    mean = sum(c * LIN[i] for i, c in enumerate(hist)) / max(sum(hist), 1)
    return math.log2(max(mean, 1e-4))


def camera(
    aperture: float | None = Form(None, gt=0, le=100),
    shutter: float | None = Form(None, gt=0, le=3600),
    iso: float | None = Form(None, gt=0, le=1_000_000),
) -> dict:
    """Phone settings the browser read from the original photo before shrinking it (which drops EXIF)."""
    return {33437: aperture, 33434: shutter, 34855: iso}


def analyze(im: Image.Image, crop: float, pan_x: float = 0, pan_y: float = 0, aspect: float | None = None, cam: dict | None = None) -> Analysis:
    ex = {**(cam or {}), **im.getexif().get_ifd(0x8769)}  # the image's own EXIF wins when present
    iso = ex.get(34855)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    n, t, iso = (float(x) if x else None for x in (ex.get(33437), ex.get(33434), iso))
    ev = math.log2(n * n / t) - math.log2(iso / 100) if n and t and iso else None
    return Analysis(aperture=n, shutter=t, iso=iso, ev=ev, log_mean=density(im, crop, pan_x, pan_y, aspect))


def filter_factor(grade: float) -> float:
    return 2 if grade >= 4 else 1  # Ilford Multigrade: 00–3½ same exposure, 4–5 double


def exposure(b: Analysis, bp: BenchPrint, n: Analysis, np: Print) -> dict:
    matched = b.ev is not None and n.ev is not None
    density = (b.ev + b.log_mean) - (n.ev + n.log_mean) if matched else b.log_mean - n.log_mean
    stops = {
        "density": density,
        "brightness": -(np.brightness - bp.brightness),  # brighter preview = less paper exposure
        "filter": math.log2(filter_factor(np.grade) / filter_factor(bp.grade)),
        "height": 2 * math.log2(np.height / bp.height),  # inverse square on lens-to-paper distance
        "aperture": 2 * math.log2(np.aperture / bp.aperture),
    }
    return {"seconds": bp.time * 2 ** sum(stops.values()), "stops": stops, "exif_matched": matched}


def load_settings() -> Settings:
    return Settings.model_validate_json(SETTINGS.read_text()) if SETTINGS.exists() else Settings()


def remeasure(rec: dict, crop: float) -> None:
    """Re-measure the stored benchmark after its crop or pan changes, so it matches new negatives."""
    if rec["analysis"] and IMAGE.exists():
        p = BenchPrint(**rec["print"])
        with Image.open(IMAGE) as im:
            rec["analysis"]["log_mean"] = density(im, crop, p.pan_x, p.pan_y, box_aspect(p.rotation, p.portrait))


def load() -> dict:
    if not RECORD.exists():
        return {"analysis": None, "print": BenchPrint().model_dump()}
    return json.loads(RECORD.read_text())


def write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


def save(rec: dict) -> None:
    write_json(RECORD, rec)


def save_jpeg(im: Image.Image, path: Path) -> None:
    small = ImageOps.exif_transpose(im).convert("RGB")
    small.thumbnail((1600, 1600))
    small.save(path, "JPEG", quality=88)


async def read_upload(file: UploadFile) -> Image.Image:
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Photo is too large.")
    return ImageOps.exif_transpose(open_image(raw))  # upright, so the crop box is measured the way it's shown


app = FastAPI(title="Darkroom Exposure API")


@app.post("/api/analyze")
async def post_analyze(
    file: UploadFile,
    pan_x: float = Form(0, ge=-1, le=1),
    pan_y: float = Form(0, ge=-1, le=1),
    rotation: int = Form(0, ge=0, le=3),
    portrait: bool = Form(False),
    keep: bool = Form(False),  # store a small copy as the new negative
    cam: dict = Depends(camera),
) -> Analysis:
    im = await read_upload(file)
    a = analyze(im, load_settings().autocrop, pan_x, pan_y, box_aspect(rotation, portrait), cam)
    if keep:
        save_jpeg(im, NEGATIVE)
    return a


@app.get("/api/negative/image")
def get_negative_image():
    if not NEGATIVE.exists():
        raise HTTPException(404, "No negative.")
    return FileResponse(NEGATIVE, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/benchmark")
def get_benchmark() -> dict:
    return load()


@app.put("/api/benchmark/image")
async def put_benchmark_image(file: UploadFile, cam: dict = Depends(camera)) -> Analysis:
    im = await read_upload(file)
    rec = load()
    p = BenchPrint(**rec["print"])
    a = analyze(im, load_settings().autocrop, p.pan_x, p.pan_y, box_aspect(p.rotation, p.portrait), cam)
    save_jpeg(im, IMAGE)
    rec["analysis"] = a.model_dump()
    save(rec)
    return a


@app.get("/api/benchmark/image")
def get_benchmark_image():
    if not IMAGE.exists():
        raise HTTPException(404, "No benchmark image.")
    return FileResponse(IMAGE, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.put("/api/benchmark/print")
def put_benchmark_print(p: BenchPrint) -> dict:
    rec = load()
    rec["print"] = p.model_dump()
    remeasure(rec, load_settings().autocrop)
    save(rec)
    return rec


@app.delete("/api/benchmark", status_code=204)
def delete_benchmark() -> None:
    RECORD.unlink(missing_ok=True)
    IMAGE.unlink(missing_ok=True)


@app.post("/api/exposure")
def post_exposure(req: ExposureIn) -> dict:
    rec = load()
    bp = BenchPrint(**rec["print"])
    if not rec["analysis"] or not bp.time or not bp.height:
        raise HTTPException(409, "Benchmark needs a photo, time and height.")
    return exposure(Analysis(**rec["analysis"]), bp, req.analysis, req.print)


# ---------- Completed prints ----------
def load_print(pid: str) -> dict:
    f = PRINTS / f"{pid}.json"
    if not f.exists():
        raise HTTPException(404, "Print not found.")
    return json.loads(f.read_text())


@app.get("/api/prints")
def list_prints() -> list[dict]:
    recs = (json.loads(f.read_text()) for f in PRINTS.glob("*.json"))
    return sorted(recs, key=lambda r: r["created"], reverse=True)


@app.post("/api/prints", status_code=201)
async def create_print(file: UploadFile, data: str = Form(), cam: dict = Depends(camera)) -> dict:
    try:
        p = SavedPrint.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "Check the print settings.")
    im = await read_upload(file)
    pid = uuid4().hex
    rec = {"id": pid, "created": datetime.now(timezone.utc).isoformat(), "analysis": analyze(im, load_settings().autocrop, p.pan_x, p.pan_y, box_aspect(p.rotation, p.portrait), cam).model_dump(), "print": p.model_dump()}
    save_jpeg(im, PRINTS / f"{pid}.jpg")
    write_json(PRINTS / f"{pid}.json", rec)
    return rec


@app.put("/api/prints/{pid}")
def update_print(pid: PrintId, p: SavedPrint) -> dict:
    rec = load_print(pid)
    rec["print"] = p.model_dump()
    with Image.open(PRINTS / f"{pid}.jpg") as im:  # keep density in step with the print's crop
        rec["analysis"]["log_mean"] = density(im, load_settings().autocrop, p.pan_x, p.pan_y, box_aspect(p.rotation, p.portrait))
    write_json(PRINTS / f"{pid}.json", rec)
    return rec


@app.get("/api/prints/{pid}/image")
def get_print_image(pid: PrintId):
    load_print(pid)
    return FileResponse(PRINTS / f"{pid}.jpg", media_type="image/jpeg")


@app.delete("/api/prints/{pid}", status_code=204)
def delete_print(pid: PrintId) -> None:
    load_print(pid)
    (PRINTS / f"{pid}.json").unlink()
    (PRINTS / f"{pid}.jpg").unlink(missing_ok=True)


# ---------- Settings ----------
@app.get("/api/settings")
def get_settings() -> Settings:
    return load_settings()


@app.put("/api/settings")
def put_settings(s: Settings) -> Settings:
    write_json(SETTINGS, s.model_dump())
    rec = load()
    remeasure(rec, s.autocrop)
    save(rec)
    return s
