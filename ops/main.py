from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request

from ops import graph3d, history, state
from ops.checks import close_neo4j_driver
from ops.state import ALL_CHECKS
from ops.config import ops_settings as cfg
from ops.metrics import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 첫 화면이 비어 있지 않도록 한 사이클 먼저 돌린다. 실패해도 폴러는 계속 돈다.
    try:
        await state.poll_once()
    except Exception:
        logging.getLogger("ops").exception("initial poll failed")

    task = asyncio.create_task(state.poller())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        close_neo4j_driver()
        history.close()


app = FastAPI(title="LawGraphRAG Ops", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {"poll_interval": cfg.poll_interval_seconds})


@app.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request):
    return templates.TemplateResponse(request, "graph3d.html", {})


# --------------------------------------------------------------------------
# 상태 API
# --------------------------------------------------------------------------

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    """감시 대상이 아니라 ops 서비스 자신의 생존 신호다."""
    return "ok"


@app.get("/api/status")
async def api_status():
    snapshot = state.get_snapshot()
    results = snapshot.get("results") or {}
    uptimes = {name: history.uptime_ratio(name, hours=24) for name in results}
    # 결과가 비어 있는 건(첫 폴링 전) 정상이 아니라 '아직 모름'이다. True 로 새지 않게 한다.
    overall = bool(results) and all(r.get("ok") for r in results.values())
    return {**snapshot, "uptime_24h": uptimes, "overall_ok": overall}


@app.post("/api/refresh")
async def api_refresh():
    return await state.poll_once()


@app.get("/api/history/{component}")
async def api_history(component: str, hours: int = Query(6, ge=1, le=168)):
    if component not in ALL_CHECKS:
        raise HTTPException(status_code=404, detail="unknown component")
    return {"component": component, "hours": hours, "samples": history.series(component, hours)}


@app.get("/api/gauge/{name}")
async def api_gauge(name: str, hours: int = Query(24, ge=1, le=168)):
    return {"name": name, "hours": hours, "points": history.gauge_series(name, hours)}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        generate_latest(registry).decode("utf-8"), media_type=CONTENT_TYPE_LATEST
    )


# --------------------------------------------------------------------------
# 3D 그래프 API
# --------------------------------------------------------------------------

@app.get("/api/graph/overview")
async def api_graph_overview(limit: int = Query(None, ge=1, le=20000)):
    try:
        return await asyncio.to_thread(graph3d.overview, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j 조회 실패: {exc}") from exc


@app.get("/api/graph/expand")
async def api_graph_expand(node_id: str, limit: int = Query(300, ge=1, le=5000)):
    try:
        return await asyncio.to_thread(graph3d.expand, node_id, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j 조회 실패: {exc}") from exc


@app.get("/api/graph/search")
async def api_graph_search(q: str = ""):
    try:
        return {"results": await asyncio.to_thread(graph3d.search_laws, q)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j 조회 실패: {exc}") from exc
