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
        '<label class="pw">Password'
        '<input type="password" name="password" required autocomplete="current-password">'
        "</label>"
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pitch2onepager — TEN Capital</title>
<style>
  :root {
    --navy: #1B2A4A;
    --orange: #E85D26;
    --lightblue: #C8D6E8;
    --bg: #F7F9FC;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    padding: 2rem 1rem; background: var(--bg); color: var(--navy);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }
  main { width: 100%; max-width: 34rem; }
  h1 { margin: 0 0 .35rem; font-size: 1.5rem; letter-spacing: -.01em; }
  .sub { margin: 0 0 1.75rem; color: #5A6B85; font-size: .95rem; }
  form {
    background: #fff; border: 1px solid var(--lightblue); border-radius: 12px;
    padding: 1.5rem; box-shadow: 0 1px 3px rgba(27,42,74,.06);
  }
  .drop {
    display: block; border: 2px dashed var(--lightblue); border-radius: 10px;
    padding: 2rem 1rem; text-align: center; cursor: pointer;
    transition: border-color .15s, background .15s;
  }
  .drop:hover, .drop.over { border-color: var(--orange); background: #FFF6F2; }
  .drop strong { display: block; font-size: 1rem; }
  .drop span { color: #5A6B85; font-size: .85rem; }
  input[type=file] { display: none; }
  .pw { display: block; margin-top: 1rem; font-size: .85rem; font-weight: 600; }
  .pw input {
    display: block; width: 100%; margin-top: .35rem; padding: .6rem .7rem;
    font: inherit; font-size: .9rem; border: 1px solid var(--lightblue); border-radius: 8px;
  }
  button {
    width: 100%; margin-top: 1rem; padding: .8rem; font: inherit; font-weight: 600;
    color: #fff; background: var(--orange); border: 0; border-radius: 8px; cursor: pointer;
  }
  button:disabled { opacity: .55; cursor: progress; }
  .status { margin-top: 1rem; font-size: .9rem; min-height: 1.4em; }
  .status.error { color: #B3300B; }
  footer { margin-top: 1.25rem; text-align: center; font-size: .75rem; color: #7A8AA3; }
</style>
</head>
<body>
<main>
  <h1>Pitch deck &rarr; one-pager</h1>
  <p class="sub">Upload a <strong>.pdf</strong> or <strong>.pptx</strong> deck. You'll get back a
  single-page Customer Journey Market Narrative.</p>

  <form id="f">
    <label class="drop" id="drop">
      <strong id="label">Choose a deck or drop it here</strong>
      <span>PDF or PPTX &middot; up to 25 MB</span>
      <input type="file" id="file" name="file" accept=".pdf,.pptx" required>
    </label>
    <!--PASSWORD-->
    <button type="submit" id="go">Generate one-pager</button>
    <div class="status" id="status" role="status"></div>
  </form>

  <footer>Compiled by TEN Capital Network</footer>
</main>
<script>
  const form = document.getElementById('f');
  const input = document.getElementById('file');
  const drop = document.getElementById('drop');
  const label = document.getElementById('label');
  const go = document.getElementById('go');
  const status = document.getElementById('status');

  input.addEventListener('change', () => {
    if (input.files.length) label.textContent = input.files[0].name;
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
      label.textContent = input.files[0].name;
    }
  });

  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    go.disabled = true;
    status.className = 'status';
    status.textContent = 'Reading deck and analysing with Claude — this takes 30-60 seconds…';
    try {
      const res = await fetch('/generate', { method: 'POST', body: new FormData(form) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Request failed (${res.status}).`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const name = (res.headers.get('Content-Disposition') || '').match(/filename="(.+?)"/);
      const a = document.createElement('a');
      a.href = url;
      a.download = name ? name[1] : 'onepager.pdf';
      a.click();
      window.open(url, '_blank');
      status.textContent = 'Done — your one-pager has been downloaded.';
    } catch (err) {
      status.className = 'status error';
      status.textContent = err.message;
    } finally {
      go.disabled = false;
    }
  });
</script>
</body>
</html>
"""

__all__ = ["app"]
