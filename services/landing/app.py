"""
Landing Service — simple home page linking to all Frostbite services.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Landing Service")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    html_path = Path("/app/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Landing page not found</h1>")


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
