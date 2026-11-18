// Shared by index.html and prints.html.
const APS = [2.8, 3.4, 4, 4.8, 5.6, 6.7, 8, 9.5, 11, 13, 16, 22];
const GRADES = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];
const $ = (id) => document.getElementById(id);
const half = (g) => (Math.floor(g) || (g % 1 ? '' : '0')) + (g % 1 ? '½' : '');
const signed = (s, unit = '') => Math.abs(s) < 0.05 ? '±0' + unit : (s > 0 ? '+' : '−') + Math.abs(s).toFixed(1) + unit;
const fmtTime = (t) => t >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')} min` : `${t < 10 ? t.toFixed(1) : Math.round(t)} s`;

// Invert the negative for preview. CSS brightness scales sRGB values, so 2^(stops/2.2) ≈ that many stops of light.
const lookFilter = (brightness, grade) => `invert(1) grayscale(1) brightness(${2 ** (brightness / 2.2)}) contrast(${2 ** ((grade - 2.5) * 0.35)})`;

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
  return { time: v('time'), height: v('height'), aperture: v('aperture'), grade: v('grade'), brightness: Math.round(v('brightness') * 3) / 3 };
}

async function api(path, opts = {}) {
  if (opts.json) opts = { method: opts.method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(opts.json) };
  const r = await fetch('/api' + path, opts);
  const body = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'Check the values you entered.');
  return body;
}
