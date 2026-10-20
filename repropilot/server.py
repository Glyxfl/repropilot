"""Local single-user prototype. Tool execution is out-of-process, not arbitrary code."""
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import store
from .config import ROOT, RUNTIME, WEB
from .datasets import CATALOG
from .models import Approval, TaskRequest
from .planner import PlanningError, make_plan

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-run")
planning_slots = threading.BoundedSemaphore(2)
ARTIFACTS = {"delivery.zip", "volcano.png", "pca.png", "volcano.svg", "pca.svg", "report.html", "report.md",
             "differential_expression.csv", "normalized_counts.csv", "manifest.json", "trace.json", "reproduce.py", "plan.json", "summary.json", "environment.txt", "counts.csv", "metadata.csv"}


def execute(run_id):
    if not store.transition(run_id, "queued", "running"):
        return
    folder = RUNTIME / run_id
    folder.mkdir(parents=True, exist_ok=True)
    # Secret-free worker environment. CPU and wall-clock limits are explicit.
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("TOKEN", "SECRET", "API_KEY", "PASSWORD"))}
    env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8", "MPLCONFIGDIR": str(RUNTIME / "matplotlib")})
    try:
        with (folder / "worker.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "repropilot.worker", run_id], cwd=ROOT, env=env,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            try:
                code = process.wait(timeout=300)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("执行超过 300 秒限制，工作进程已终止；可检查记录后重试。")
        if code and store.get(run_id)["status"] == "running":
            raise RuntimeError("分析工作进程异常退出。请检查本地 worker.log。")
    except Exception as exc:
        store.event(run_id, "executor", "failed", str(exc))
        store.transition(run_id, "running", "failed", error=str(exc))


@asynccontextmanager
async def lifespan(app):
    store.init()
    store.recover_interrupted()
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="ReproPilot Research Execution API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "This prototype is loopback-only."}, status_code=403)
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            return JSONResponse({"detail": "Cross-origin mutation rejected."}, status_code=403)
        try:
            if int(request.headers.get("content-length", 0)) > 16384:
                return JSONResponse({"detail": "Request too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def get_run(run_id):
    if not re.fullmatch(r"[a-f0-9]{12}", run_id):
        raise HTTPException(404, "任务不存在")
    result = store.get(run_id)
    if not result:
        raise HTTPException(404, "任务不存在")
    return result


@app.get("/api/health")
def health():
    return {"status": "ok", "live_model_configured": all(os.environ.get(k) for k in ["MODEL_API_KEY", "MODEL_NAME", "MODEL_BASE_URL"])}


@app.get("/api/datasets")
def datasets():
    return list(CATALOG.values())


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.post("/api/runs", status_code=201)
def create(request: TaskRequest):
    if not planning_slots.acquire(blocking=False):
        raise HTTPException(429, "当前规划任务较多，请稍后再试。")
    try:
        plan = make_plan(request)
    except (PlanningError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        planning_slots.release()
    return store.create(request.prompt, plan.model_dump(), request.planner, request.recovery_demo)


@app.get("/api/runs/{run_id}")
def detail(run_id: str):
    return get_run(run_id)


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, approval: Approval):
    run = get_run(run_id)
    if approval.plan_hash != run["plan_hash"]:
        raise HTTPException(409, "方案已变化，请重新确认。")
    if not store.transition(run_id, "awaiting_approval", "queued"):
        raise HTTPException(409, "此任务已确认或无法执行。")
    store.event(run_id, "approval", "complete", "研究者已确认冻结方案，任务进入执行队列。", {"plan_hash": approval.plan_hash})
    executor.submit(execute, run_id)
    return get_run(run_id)


@app.post("/api/runs/{run_id}/retry")
def retry(run_id: str):
    get_run(run_id)
    if not store.transition(run_id, "failed", "queued", error=None):
        raise HTTPException(409, "仅失败任务可以重试。")
    store.event(run_id, "executor", "running", "重试已入队，将验证检查点后恢复。")
    executor.submit(execute, run_id)
    return get_run(run_id)


@app.get("/api/runs/{run_id}/artifacts/{name}")
def artifact(run_id: str, name: str):
    run = get_run(run_id)
    if run["status"] != "succeeded" or name not in ARTIFACTS:
        raise HTTPException(404, "交付物尚未就绪")
    path = RUNTIME / run_id / name
    if not path.is_file():
        raise HTTPException(404, "交付物不存在")
    return FileResponse(path, filename=name if name.endswith(("zip", "csv", "py")) else None)


@app.get("/")
def home():
    return FileResponse(WEB / "index.html")


app.mount("/assets", StaticFiles(directory=WEB), name="assets")
