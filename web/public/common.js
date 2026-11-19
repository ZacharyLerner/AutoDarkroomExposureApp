// Shared by index.html and prints.html.
const APS = [2.8, 3.4, 4, 4.8, 5.6, 6.7, 8, 9.5, 11, 13, 16, 22];
const GRADES = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];
const $ = (id) => document.getElementById(id);
const half = (g) => (Math.floor(g) || (g % 1 ? '' : '0')) + (g % 1 ? '½' : '');
const signed = (s, unit = '') => Math.abs(s) < 0.05 ? '±0' + unit : (s > 0 ? '+' : '−') + Math.abs(s).toFixed(1) + unit;
const fmtTime = (t) => t >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')} min` : `${t < 10 ? t.toFixed(1) : Math.round(t)} s`;

// ---------- Paper simulation ----------
const LOG = Array.from({ length: 256 }, (_, i) => {
  const x = i / 255, lin = x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; // same linearisation as the server
  return Math.log2(Math.max(lin, 1e-4));
});

// Photo -> 8-bit luminance (server's weights), at most 1600px.
function loadNegative(src) {
  return new Promise((res, rej) => {
    const im = new Image();
    const done = () => { if (src.startsWith('blob:')) URL.revokeObjectURL(src); };
    im.onload = () => {
      done();
      const s = Math.min(1, 1600 / Math.max(im.naturalWidth, im.naturalHeight));
      const w = Math.round(im.naturalWidth * s), h = Math.round(im.naturalHeight * s);
      const cv = document.createElement('canvas');
      cv.width = w; cv.height = h;
      const cx = cv.getContext('2d');
      cx.drawImage(im, 0, 0, w, h);
      const d = cx.getImageData(0, 0, w, h).data, lum = new Uint8Array(w * h);
      for (let i = 0, j = 0; j < lum.length; i += 4, j++) lum[j] = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
      res({ w, h, lum });
    };
    im.onerror = () => { done(); rej(new Error("That photo couldn't be displayed.")); };
    im.src = src;
  });
}

// Phone settings from a JPEG's EXIF ({} if absent or not a JPEG). Only these three are needed.
async function readExif(file) {
  try {
    const v = new DataView(await file.slice(0, 1 << 18).arrayBuffer());
    if (v.getUint16(0) !== 0xFFD8) return {};
    for (let p = 2; v.getUint16(p) !== 0xFFDA; p += 2 + v.getUint16(p + 2)) { // walk markers until image data
      if (v.getUint16(p) !== 0xFFE1 || v.getUint32(p + 4) !== 0x45786966) continue; // APP1 "Exif"
      const t = p + 10, le = v.getUint16(t) === 0x4949;
      const u16 = (o) => v.getUint16(t + o, le), u32 = (o) => v.getUint32(t + o, le);
      const tags = (ifd) => { const m = {}; for (let i = 0; i < u16(ifd); i++) m[u16(ifd + 2 + 12 * i)] = ifd + 2 + 12 * i; return m; };
      const sub = tags(u32(4))[0x8769];
      if (!sub) return {};
      const x = tags(u32(sub + 8));
      const rat = (e) => e && u32(u32(e + 8) + 4) ? u32(u32(e + 8)) / u32(u32(e + 8) + 4) : null;
      return { aperture: rat(x[0x829D]), shutter: rat(x[0x829A]), iso: x[0x8827] ? u16(x[0x8827] + 8) : null };
    }
  } catch { /* truncated or odd file: no settings */ }
  return {};
}

// Upright ~1600px JPEG for upload and storage; the full-size photo never leaves the phone.
async function shrink(file) {
  const exif = await readExif(file);
  let bmp;
  try { bmp = await createImageBitmap(file); } catch { throw new Error("That file couldn't be opened as an image."); }
  const s = Math.min(1, 1600 / Math.max(bmp.width, bmp.height)), cv = document.createElement('canvas');
  cv.width = Math.round(bmp.width * s); cv.height = Math.round(bmp.height * s);
  cv.getContext('2d').drawImage(bmp, 0, 0, cv.width, cv.height);
  bmp.close();
  return { blob: await new Promise((r) => cv.toBlob(r, 'image/jpeg', 0.9)), exif };
}

// Stops of negative that span paper white to black: Ilford-style ISO R ≈ 160 at grade 0 down to 50 at grade 5.
const paperStops = (grade) => (160 - 22 * grade) / 100 / Math.log10(2);

// Draw the negative as a print. Exposure is relative to the photo's measured mean (what the calculated
// time equalises), so the preview predicts the print at that time; brightness = stops less paper exposure.
// CONTRAST stretches the photo's tones back out: phones record a negative's ~3-4 stops as ~1.5.
function renderPrint(canvas, neg, logMean, brightness, grade) {
  const range = paperStops(grade), lut = new Uint8ClampedArray(256);
  for (let i = 0; i < 256; i++) {
    const t = Math.min(1, Math.max(0, (CONTRAST * (LOG[i] - logMean) - brightness) / range + 0.5));
    lut[i] = 255 * (1 - t * t * (3 - 2 * t)); // smoothstep: soft toe and shoulder, like paper
  }
  if (canvas.width !== neg.w || canvas.height !== neg.h) { canvas.width = neg.w; canvas.height = neg.h; }
  const cx = canvas.getContext('2d'), out = cx.createImageData(neg.w, neg.h), o = out.data;
  for (let j = 0, i = 0; j < neg.lum.length; j++, i += 4) { o[i] = o[i + 1] = o[i + 2] = lut[neg.lum[j]]; o[i + 3] = 255; }
  cx.putImageData(out, 0, 0);
}

// The crop box is 3:2 (2:3 when portrait). Mirrors box_aspect/density in api/main.py:
// fw/fh = box size as a fraction of the photo (largest fit, zoomed by autocrop), sx/sy = pan slack.
function cropGeom(w, h, r, portrait) {
  const box = portrait ? 2 / 3 : 3 / 2, a = r % 2 ? 1 / box : box, f = Math.max(1 - CROP / 100, 0.01);
  const [fw, fh] = w / h > a ? [(h * a) / w, 1] : [1, w / (a * h)];
  return { box, f, sx: (1 - fw * f) / 2, sy: (1 - fh * f) / 2 };
}

// Show photo r quarter turns counterclockwise, covering the 3:2 frame (img's parent), zoomed by
// autocrop and shifted by pan. The img is sized pre-rotation so the rotated photo covers exactly.
function placeRotated(img, r, px = 0, py = 0, portrait = false) {
  const w = img.naturalWidth || img.width || 4, h = img.naturalHeight || img.height || 3, odd = r % 2;
  const g = cropGeom(w, h, r, portrait), R = odd ? h / w : w / h;
  const [cw, ch] = R > g.box ? [R / g.box, 1] : [1, g.box / R]; // rotated photo size, in frame widths/heights
  img._g = g;
  img.parentElement.style.aspectRatio = g.box;
  Object.assign(img.style, {
    position: 'absolute', left: '50%', top: '50%',
    width: `${(odd ? ch / g.box : cw) * 100}%`, height: `${(odd ? cw * g.box : ch) * 100}%`,
    transform: `translate(-50%, -50%) rotate(${-90 * r}deg) scale(${1 / g.f}) translate(${-px * g.sx * 100}%, ${-py * g.sy * 100}%)`,
    touchAction: g.sx > 0.001 || g.sy > 0.001 ? 'none' : '', cursor: g.sx > 0.001 || g.sy > 0.001 ? 'grab' : '',
  });
}
function onRotate(form, btn) {
  btn.onclick = () => { form.rotation.value = (+form.rotation.value + 1) % 4; form.dispatchEvent(new Event('input')); };
}
function onBox(form, btn) {
  btn.onclick = () => { form.portrait.value = form.portrait.value === '1' ? '0' : '1'; form.dispatchEvent(new Event('input')); };
}
const boxLabel = (p) => (p.portrait ? 'Box 2:3' : 'Box 3:2');

let CROP = 0, CONTRAST = 2.2;
const settingsReady = api('/settings').then((s) => { CROP = s.autocrop; CONTRAST = s.contrast; }).catch(() => {});

// Drag the photo to move the crop box over it. onMove redraws; onEnd saves/re-measures.
function enablePan(img, form, onMove, onEnd) {
  let start = null, moved = false;
  img.draggable = false;
  img.addEventListener('pointerdown', (e) => {
    const g = img._g;
    if (!g || (g.sx <= 0.001 && g.sy <= 0.001)) return;
    start = { x: e.clientX, y: e.clientY, px: +form.pan_x.value, py: +form.pan_y.value };
    moved = false;
    img.setPointerCapture(e.pointerId);
  });
  img.addEventListener('pointermove', (e) => {
    if (!start) return;
    const sx = e.clientX - start.x, sy = e.clientY - start.y;
    if (!moved && Math.hypot(sx, sy) < 4) return;
    moved = true;
    // Screen drag -> photo's own axes: undo the rotation and the crop zoom.
    const g = img._g, a = (+form.rotation.value * Math.PI) / 2;
    const vx = (sx * Math.cos(a) - sy * Math.sin(a)) * g.f, vy = (sx * Math.sin(a) + sy * Math.cos(a)) * g.f;
    const clamp = (v) => Math.round(Math.max(-1, Math.min(1, v)) * 1000) / 1000;
    if (g.sx > 0.001) form.pan_x.value = clamp(start.px - vx / (g.sx * img.offsetWidth));
    if (g.sy > 0.001) form.pan_y.value = clamp(start.py - vy / (g.sy * img.offsetHeight));
    onMove();
  });
  const end = () => { if (start && moved) onEnd(); start = null; };
  img.addEventListener('pointerup', end);
  img.addEventListener('pointercancel', end);
  img.addEventListener('click', (e) => { if (moved) { e.preventDefault(); moved = false; } }); // a drag isn't a tap
}

const exifLine = (a) => a.ev == null
  ? 'No exposure data in this photo, so density assumes identical phone settings.'
  : `f/${+a.aperture.toFixed(1)} · 1/${Math.round(1 / a.shutter)} s · ISO ${Math.round(a.iso)} · EV ${a.ev.toFixed(1)}`;

// apDefault: aperture selected when the form loads/resets (null = leave first option).
function fillSelects(form, apDefault) {
  APS.forEach((a) => form.aperture.add(new Option('f/' + a, a, a === apDefault, a === apDefault)));
  GRADES.forEach((g) => form.grade.add(new Option(half(g) + (g === 2.5 ? ' (none)' : ''), g, g === 2.5, g === 2.5)));
  form.onsubmit = (e) => e.preventDefault();
}

function readPrint(form) {
  const v = (n) => form[n] && form[n].value !== '' ? +form[n].value : null;
  return { time: v('time'), height: v('height'), aperture: v('aperture'), grade: v('grade'), brightness: Math.round(v('brightness') * 3) / 3, rotation: v('rotation') ?? 0, pan_x: v('pan_x') ?? 0, pan_y: v('pan_y') ?? 0, portrait: v('portrait') === 1 };
}

async function api(path, opts = {}) {
  if (opts.json) opts = { method: opts.method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(opts.json) };
  const r = await fetch('/api' + path, opts);
  const body = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'Check the values you entered.');
  return body;
}

// Coming back via Back can restore a frozen copy of the page with stale settings; reload instead.
addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); });
