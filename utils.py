"""通用小工具：JSON 读写、哈希、日志落盘。"""
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import config


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def read_json(path: Path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, data) -> None:
    """原子写，避免中断产生半截文件。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def append_jsonl(path: Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_bytes(*parts: bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()


def log_line(channel: str, message: str, **extra) -> None:
    """四类日志 + 通用日志，统一写 logs/<channel>.log。"""
    config.ensure_dirs()
    row = {"ts": now_iso(), "channel": channel, "message": message}
    if extra:
        row["extra"] = extra
    line = json.dumps(row, ensure_ascii=False)
    with (config.LOG_DIR / f"{channel}.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
