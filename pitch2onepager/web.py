"""HTTP wrapper around the CLI pipeline: upload a deck, get the one-pager PDF.

This exists so the tool can run as a long-lived service (Railway/railpack needs
a start command). It is a thin shell over ``extract_deck`` → ``analyze_deck`` →
``build_onepager`` — no analysis logic lives here.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import __version__
from .analyzer import analyze_deck
from .builder import build_onepager
from .extractor import extract_deck
from .utils import (
    SUPPORTED_EXTENSIONS,
    AnalysisError,
    APIError,
    BuildError,
    ExtractionError,
    FileError,
    slugify,
)

# Decks above this are almost always image-only exports, which the analyser
# rejects anyway — refuse them before spending memory on the upload.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(
    title="pitch2onepager",
    version=__version__,
    description="Turn an investor pitch deck into a one-page Customer Journey narrative.",
)


# --------------------------------------------------------------------------- #
# Access gate
# --------------------------------------------------------------------------- #


def _required_password() -> str | None:
    """The shared password, if one is configured.

    A public URL that spends Anthropic credits on every request wants a lock on
    it. Set ``APP_PASSWORD`` in the environment to require one; leave it unset
    and the service is open.
    """
    return os.environ.get("APP_PASSWORD") or None


def _check_password(supplied: str | None) -> None:
    expected = _required_password()
    if expected is None:
        return
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Incorrect password.")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """Liveness probe for the platform health check."""
    return {
        "status": "ok",
        "version": __version__,
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_PAGE.replace("<!--PASSWORD-->", _password_field()))


@app.post("/generate")
async def generate(file: UploadFile, password: str | None = Form(default=None)) -> Response:
    """Run the full pipeline on an uploaded deck and return the rendered PDF."""
    _check_password(password)

    name = Path(file.filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or '(none)'}'. Supported formats: {supported}",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Deck is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    pdf_bytes, filename = await run_in_threadpool(_run_pipeline, payload, suffix)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _run_pipeline(payload: bytes, suffix: str) -> tuple[bytes, str]:
    """Extract → analyse → render, entirely inside a scratch directory.

    Returns the PDF bytes (read back before the directory is torn down) and the
    download filename.
    """
    with tempfile.TemporaryDirectory(prefix="pitch2onepager-") as tmp:
        deck_path = Path(tmp) / f"deck{suffix}"
        deck_path.write_bytes(payload)

        try:
            content = extract_deck(str(deck_path))
            analysis = analyze_deck(content)
            out_path = Path(tmp) / f"{slugify(analysis.company_name)}_onepager.pdf"
            build_onepager(analysis, str(out_path))
        except FileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ExtractionError, AnalysisError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except APIError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BuildError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return out_path.read_bytes(), out_path.name


@app.exception_handler(HTTPException)
async def _http_error(_request: object, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# --------------------------------------------------------------------------- #
# Upload page
# --------------------------------------------------------------------------- #


def _password_field() -> str:
    if _required_password() is None:
        return ""
    return (
        '<label class="pw">Access password'
        '<input type="password" name="password" required autocomplete="current-password">'
        "</label>"
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deck to One-Pager &middot; TEN Capital Network</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --navy-950:#0B1526; --navy-900:#101E33; --navy-800:#16283F; --navy-700:#1E354F;
    --coral:#EE5A4E; --coral-soft:#F0776C; --amber:#F3A22A; --teal:#35BEBB;
    --ink-100:#F3F6FA; --ink-300:#C4D0E0; --ink-500:#7E90A8; --ink-600:#5C6E86;
    --sans:'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    --display:'Sora', var(--sans);
    --mono:'JetBrains Mono', ui-monospace, SFMono-Regular, Consolas, monospace;
  }

  *{ box-sizing:border-box; }

  html, body{
    margin:0; padding:0;
    background:var(--navy-950); color:var(--ink-100);
    font-family:var(--sans);
    min-height:100vh;
  }

  body{
    display:flex; align-items:center; justify-content:center;
    padding:48px 20px;
    position:relative; overflow-x:hidden;
  }

  /* ambient tri-color glow, echoing the logo's three figures */
  body::before{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
    background:
      radial-gradient(480px 380px at 14% 8%, rgba(238,90,78,0.16), transparent 60%),
      radial-gradient(480px 380px at 86% 6%, rgba(243,162,42,0.13), transparent 60%),
      radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.14), transparent 60%);
  }

  .stage{ position:relative; z-index:1; width:100%; max-width:620px; }

  /* brand lockup */
  .brand{ display:flex; align-items:center; gap:12px; margin-bottom:28px; padding-left:4px; }
  .brand-mark{ width:34px; height:34px; flex-shrink:0; }
  .brand-word{
    font-family:var(--display); font-weight:800; font-size:15px;
    letter-spacing:0.04em; line-height:1.15; text-transform:uppercase;
  }
  .brand-word span{
    display:block; font-weight:600; font-size:10px; letter-spacing:0.22em;
    color:var(--ink-500); margin-top:2px;
  }

  .card{
    background:linear-gradient(180deg, var(--navy-900) 0%, var(--navy-800) 100%);
    border:1px solid var(--navy-700); border-radius:20px;
    padding:44px 44px 36px;
    box-shadow:0 30px 60px -20px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.03);
    position:relative; overflow:hidden;
  }
  .card::after{
    content:""; position:absolute; top:-2px; left:44px; right:44px; height:2px;
    background:linear-gradient(90deg, var(--coral), var(--amber), var(--teal));
    border-radius:2px;
  }

  .eyebrow{
    display:flex; align-items:center; gap:8px;
    font-family:var(--mono); font-size:11px; letter-spacing:0.14em;
    text-transform:uppercase; color:var(--teal); margin-bottom:14px;
  }
  .eyebrow::before{
    content:""; width:6px; height:6px; border-radius:50%;
    background:var(--teal); box-shadow:0 0 0 3px rgba(53,190,187,0.18);
  }

  h1{
    font-family:var(--display); font-size:28px; font-weight:700; line-height:1.25;
    margin:0 0 12px; letter-spacing:-0.01em;
  }
  h1 .arrow{ color:var(--ink-500); font-weight:400; margin:0 4px; }
  h1 .to{
    background:linear-gradient(90deg, var(--coral-soft), var(--amber));
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }

  .lede{ color:var(--ink-300); font-size:15px; line-height:1.6; margin:0 0 32px; max-width:46ch; }

  /* dropzone */
  .dropzone{
    display:block;
    border:1.5px dashed var(--navy-700); border-radius:14px;
    padding:38px 24px; text-align:center; cursor:pointer;
    background:rgba(255,255,255,0.015);
    transition:border-color .18s ease, background .18s ease, transform .18s ease;
  }
  .dropzone:hover, .dropzone.over{ border-color:var(--teal); background:rgba(53,190,187,0.05); }
  .dropzone.over{ transform:scale(1.004); }
  .dropzone:active{ transform:scale(0.997); }
  .dropzone:focus-within{
    border-color:var(--teal); outline:2px solid rgba(53,190,187,0.55); outline-offset:3px;
  }
  .dropzone.loaded{ border-style:solid; border-color:var(--teal); background:rgba(53,190,187,0.05); }

  .dropzone-icon{
    width:38px; height:38px; margin:0 auto 14px; border-radius:10px;
    background:linear-gradient(135deg, rgba(238,90,78,0.16), rgba(243,162,42,0.16));
    border:1px solid var(--navy-700);
    display:flex; align-items:center; justify-content:center;
  }
  .dropzone-icon svg{ width:18px; height:18px; }
  .dropzone.loaded .dropzone-icon{
    background:linear-gradient(135deg, rgba(53,190,187,0.22), rgba(53,190,187,0.10));
    border-color:rgba(53,190,187,0.45);
  }

  .dropzone-title{ font-size:15px; font-weight:600; color:var(--ink-100); margin-bottom:6px; word-break:break-word; }
  .dropzone-sub{ font-family:var(--mono); font-size:11.5px; color:var(--ink-500); letter-spacing:0.01em; }
  .dropzone-sub b{ color:var(--ink-300); font-weight:500; }

  /* keep the input focusable for keyboard users — display:none would remove it */
  .file-input{
    position:absolute; width:1px; height:1px; opacity:0;
    margin:0; padding:0; border:0; overflow:hidden;
  }

  /* password */
  .pw{
    display:block; margin-top:20px;
    font-family:var(--mono); font-size:11px; letter-spacing:0.12em;
    text-transform:uppercase; color:var(--ink-500);
  }
  .pw input{
    display:block; width:100%; margin-top:8px; padding:13px 14px;
    font-family:var(--sans); font-size:14px; letter-spacing:normal; text-transform:none;
    color:var(--ink-100); background:var(--navy-950);
    border:1px solid var(--navy-700); border-radius:10px;
    transition:border-color .15s ease;
  }
  .pw input:focus{ outline:none; border-color:var(--teal); box-shadow:0 0 0 3px rgba(53,190,187,0.18); }

  /* CTA */
  .cta{
    width:100%; margin-top:22px; padding:16px 20px;
    border:none; border-radius:12px;
    background:linear-gradient(90deg, var(--coral) 0%, var(--coral-soft) 45%, var(--amber) 100%);
    color:#17130E;
    font-family:var(--display); font-weight:700; font-size:15px; letter-spacing:0.01em;
    cursor:pointer;
    display:flex; align-items:center; justify-content:center; gap:10px;
    transition:filter .15s ease, transform .15s ease;
    box-shadow:0 10px 24px -10px rgba(238,90,78,0.45);
  }
  .cta:hover:not(:disabled){ filter:brightness(1.06); transform:translateY(-1px); }
  .cta:active:not(:disabled){ transform:translateY(0); }
  .cta:focus-visible{ outline:2px solid var(--teal); outline-offset:3px; }
  .cta:disabled{ cursor:progress; filter:saturate(.55) brightness(.85); box-shadow:none; }

  .spinner{
    width:15px; height:15px; flex-shrink:0; display:none;
    border:2px solid rgba(23,19,14,0.28); border-top-color:#17130E;
    border-radius:50%; animation:spin .7s linear infinite;
  }
  .cta.busy .spinner{ display:block; }
  @keyframes spin{ to{ transform:rotate(360deg); } }

  /* status */
  .status{
    margin-top:16px; min-height:1.5em;
    font-family:var(--mono); font-size:12px; line-height:1.55; color:var(--ink-300);
  }
  .status:empty{ display:none; }
  .status.working{ color:var(--amber); }
  .status.done{ color:var(--teal); }
  .status.error{ color:var(--coral-soft); }

  /* footnote / disclosure */
  .disclosure{
    margin-top:22px; padding-top:18px; border-top:1px solid var(--navy-700);
    font-size:12px; line-height:1.6; color:var(--ink-500);
  }
  .disclosure code{
    font-family:var(--mono); background:var(--navy-950);
    border:1px solid var(--navy-700); color:var(--ink-300);
    padding:2px 6px; border-radius:5px; font-size:11.5px;
  }

  footer{
    text-align:center; margin-top:22px;
    font-family:var(--mono); font-size:11px; letter-spacing:0.08em;
    color:var(--ink-600); text-transform:uppercase;
  }

  @media (max-width:480px){
    .card{ padding:32px 24px 28px; }
    .card::after{ left:24px; right:24px; }
    h1{ font-size:23px; }
  }

  @media (prefers-reduced-motion: reduce){
    *{ animation-duration:.01ms !important; animation-iteration-count:1 !important;
       transition-duration:.01ms !important; }
    .spinner{ animation:none; border-top-color:transparent; }
  }
</style>
</head>
<body>

<div class="stage">

  <div class="brand">
    <svg class="brand-mark" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M50 6 C64 6 74 16 74 16" stroke="var(--amber)" stroke-width="11" stroke-linecap="round" fill="none"/>
      <path d="M76 66 C76 82 63 92 63 92" stroke="var(--teal)" stroke-width="11" stroke-linecap="round" fill="none"/>
      <path d="M24 66 C24 82 37 92 37 92" stroke="var(--coral)" stroke-width="11" stroke-linecap="round" fill="none" transform="rotate(180 50 79)"/>
      <circle cx="50" cy="20" r="11" fill="var(--amber)"/>
      <circle cx="78" cy="68" r="11" fill="var(--teal)"/>
      <circle cx="22" cy="68" r="11" fill="var(--coral)"/>
    </svg>
    <div class="brand-word">Ten Capital<span>Network</span></div>
  </div>

  <form class="card" id="f">
    <div class="eyebrow">Deck Analyzer</div>
    <h1>Pitch Deck<span class="arrow">&rarr;</span><span class="to">Investor One&#8209;Pager</span></h1>
    <p class="lede">Upload a pitch deck and get back a single-page Customer Journey Market
    Narrative, analyzed and structured by Claude.</p>

    <label class="dropzone" id="drop">
      <div class="dropzone-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="var(--ink-100)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 3v4a1 1 0 0 0 1 1h4"/>
          <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"/>
        </svg>
      </div>
      <div class="dropzone-title" id="label">Choose a deck or drop it here</div>
      <div class="dropzone-sub" id="sublabel"><b>.pdf</b> &middot; <b>.pptx</b> &nbsp;&middot;&nbsp; up to 25&nbsp;MB</div>
      <input class="file-input" type="file" id="file" name="file" accept=".pdf,.pptx" required>
    </label>

    <!--PASSWORD-->

    <button class="cta" type="submit" id="go">
      <span class="spinner" aria-hidden="true"></span>
      <span id="go-label">Generate one-pager PDF</span>
    </button>

    <div class="status" id="status" role="status" aria-live="polite"></div>

    <div class="disclosure">
      The deck is processed on the server, converted with <code>claude</code>, and discarded once
      the PDF is returned. Nothing is stored.
    </div>
  </form>

  <footer>Powered by TEN Capital Network</footer>

</div>

<script>
  const MAX_BYTES = 25 * 1024 * 1024;

  const form = document.getElementById('f');
  const input = document.getElementById('file');
  const drop = document.getElementById('drop');
  const label = document.getElementById('label');
  const sublabel = document.getElementById('sublabel');
  const go = document.getElementById('go');
  const goLabel = document.getElementById('go-label');
  const status = document.getElementById('status');

  const DEFAULT_SUB = sublabel.innerHTML;
  let timer = null;

  function setStatus(text, kind) {
    status.className = kind ? 'status ' + kind : 'status';
    status.textContent = text;
  }

  function showFile(file) {
    label.textContent = file.name;
    sublabel.textContent = (file.size / (1024 * 1024)).toFixed(1) + ' MB — click to replace';
    drop.classList.add('loaded');
  }

  function resetFile() {
    input.value = '';
    label.textContent = 'Choose a deck or drop it here';
    sublabel.innerHTML = DEFAULT_SUB;
    drop.classList.remove('loaded');
  }

  input.addEventListener('change', () => {
    if (input.files.length) { showFile(input.files[0]); setStatus(''); }
  });

  ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', ev => {
    if (ev.dataTransfer.files.length) {
      input.files = ev.dataTransfer.files;
      showFile(input.files[0]);
      setStatus('');
    }
  });

  form.addEventListener('submit', async ev => {
    ev.preventDefault();

    const file = input.files[0];
    if (!file) { setStatus('Choose a .pdf or .pptx deck first.', 'error'); return; }
    if (file.size > MAX_BYTES) {
      setStatus('That deck is ' + (file.size / (1024 * 1024)).toFixed(1) + ' MB — the limit is 25 MB.', 'error');
      return;
    }

    go.disabled = true;
    go.classList.add('busy');
    goLabel.textContent = 'Analyzing deck…';

    const started = Date.now();
    setStatus('Reading deck and analyzing with Claude — this takes 30–60 seconds…', 'working');
    timer = setInterval(() => {
      const secs = Math.round((Date.now() - started) / 1000);
      setStatus('Analyzing with Claude — ' + secs + 's elapsed…', 'working');
    }, 1000);

    try {
      const res = await fetch('/generate', { method: 'POST', body: new FormData(form) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || 'Request failed (' + res.status + ').');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const match = (res.headers.get('Content-Disposition') || '').match(/filename="(.+?)"/);
      const a = document.createElement('a');
      a.href = url;
      a.download = match ? match[1] : 'onepager.pdf';
      a.click();
      window.open(url, '_blank');
      clearInterval(timer);
      setStatus('Done — your one-pager has been downloaded.', 'done');
      resetFile();
    } catch (err) {
      clearInterval(timer);
      setStatus(err.message, 'error');
    } finally {
      clearInterval(timer);
      go.disabled = false;
      go.classList.remove('busy');
      goLabel.textContent = 'Generate one-pager PDF';
    }
  });
</script>

</body>
</html>
"""

__all__ = ["app"]
