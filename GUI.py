"""演示服务：/  /ask  /health  /demo/cases  /admin/*（FastAPI + 内建线程调度）。"""
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
import qa_service
import schema
from graph_store import load_graph
from kb import auto_add, question_bank
from utils import now_iso, read_json, write_json

app = FastAPI(title="医药知识图谱问答系统 · 方案 A", version="1.0")
STARTED = time.time()


class AskBody(BaseModel):
    question: str
    session_id: str | None = None


# ---------------- 页面与静态资源 ----------------
def _web_file(name: str, media_type: str | None = None):
    path = config.WEB_DIR / name
    if not path.exists():
        return JSONResponse({"detail": f"{name} 不存在"}, status_code=404)
    return FileResponse(path, media_type=media_type)


@app.get("/", response_class=HTMLResponse)
def index():
    path = config.WEB_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>web/index.html 缺失</h1>", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/manifest.json")
def manifest():
    return _web_file("manifest.json", "application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return _web_file("sw.js", "application/javascript")


@app.get("/icon.png")
def icon():
    return _web_file("icon.png", "image/png")


@app.get("/app", response_class=HTMLResponse)
def mobile_app():
    """移动端 APP 壳（与网页共用同一服务实例与 /ask 接口）。"""
    path = config.ROOT / "mobile" / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>mobile/index.html 缺失</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# ---------------- 核心接口 ----------------
@app.get("/health")
def health():
    graph = load_graph()
    metrics = read_json(config.METRICS_PATH, {})
    return {
        "status": "ok",
        "time": now_iso(),
        "uptime_sec": round(time.time() - STARTED, 1),
        "components": {
            "graph": {"status": "ok", **graph.stats()},
            "model": {"status": "ok" if config.MODEL_PATH.exists() else "missing",
                      "path": str(config.MODEL_PATH),
                      "ner_test_f1": metrics.get("ner_test", {}).get("f1"),
                      "intent_test_acc": metrics.get("intent_test_acc")},
            "dict": {"status": "ok" if config.DICT_PATH.exists() else "missing",
                     "path": str(config.DICT_PATH)},
            "question_bank": {"status": "ok", "total": len(question_bank.load_items())},
            "backup": read_json(config.BACKUP_STATE_PATH, {"status": "never"}),
        },
        "config": config.snapshot(),
        "intents": [{"intent": i, "name": schema.INTENT_CN[i],
                     "supported": schema.INTENT_SPECS[i].supported} for i in schema.INTENTS],
    }


@app.post("/ask")
def ask(body: AskBody):
    result = qa_service.answer_question(body.question, session_id=body.session_id)
    return JSONResponse(result)


@app.get("/demo/cases")
def demo_cases():
    return {"cases": qa_service.demo_cases(), "intents": [
        {"intent": i, "name": schema.INTENT_CN[i],
         "status": "已实现" if schema.INTENT_SPECS[i].supported else "待扩展",
         "extension": schema.INTENT_SPECS[i].extension} for i in schema.INTENTS]}


# ---------------- 知识库 / 题库 ----------------
@app.post("/admin/kb/sync")
def kb_sync():
    stats = auto_add.sync_once()
    write_json(config.DATA_DIR / "kb_sync_state.json", {"ran_at": now_iso(), **stats})
    return stats


@app.get("/admin/kb/stats")
def kb_stats():
    graph = load_graph()
    index = read_json(config.KB_INDEX_PATH, {"docs": {}})
    docs = index.get("docs", {})
    qitems = question_bank.load_items()
    last_sync = read_json(config.DATA_DIR / "kb_sync_state.json", {})
    return {
        "documents": len(docs),
        "documents_pending": sum(1 for d in docs.values() if d.get("status") == "pending"),
        "graph": graph.stats(),
        "question_bank": {"total": len(qitems),
                          "supported": sum(1 for i in qitems if i.get("supported"))},
        "coverage": question_bank.coverage(),
        "last_sync": last_sync,
        "last_regression": read_json(config.DATA_DIR / "qbank_regression.json", {}),
    }


@app.post("/admin/qbank/regress")
def qbank_regress():
    return question_bank.regress()


@app.get("/admin/qbank/coverage")
def qbank_coverage():
    return question_bank.coverage()


# ---------------- 备份 ----------------
@app.post("/admin/backup/run")
def backup_run():
    from backup.backup import run_backup
    return run_backup()


@app.get("/admin/backup/status")
def backup_status():
    return read_json(config.BACKUP_STATE_PATH, {"status": "never"})


# ---------------- 内建调度：知识库同步 + 每日备份 ----------------
_SCHED_STATE = {"last_sync": 0.0, "backup_day": ""}


def _scheduler() -> None:
    while True:
        try:
            now = time.time()
            if config.KB_SYNC_INTERVAL_MIN > 0 and now - _SCHED_STATE["last_sync"] >= config.KB_SYNC_INTERVAL_MIN * 60:
                auto_add.sync_once()
                _SCHED_STATE["last_sync"] = now
            today = datetime.now().strftime("%Y-%m-%d")
            if datetime.now().hour == config.BACKUP_CRON_HOUR and _SCHED_STATE["backup_day"] != today:
                from backup.backup import run_backup
                run_backup()
                _SCHED_STATE["backup_day"] = today
        except Exception as exc:  # 调度线程不得中断服务
            from utils import log_line
            log_line("scheduler", f"error: {exc}")
        time.sleep(60)


@app.on_event("startup")
def _startup():
    config.ensure_dirs()
    qa_service.warmup()
    if config.KB_SYNC_INTERVAL_MIN > 0:
        threading.Thread(target=_scheduler, daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
