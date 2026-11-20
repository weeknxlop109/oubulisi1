"""备份：打包 -> 敏感扫描 -> （DRY_RUN 则止步）-> git 提交推送。

默认 BACKUP_DRY_RUN=true：只产出 backup-YYYYMMDD-HHMM.tar.gz 与扫描报告，不推送。
GitPython 未安装，改用 git 命令行，避免新增依赖。
"""
import json
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

import config
import schema
from backup.sensitive import scan_files
from utils import append_jsonl, log_line, now_iso, read_json, write_json

INCLUDE_FILES = ("requirements.txt", ".env.example", ".gitignore", ".gitattributes",
                 "up.ps1", "verify.ps1", "up.sh", "verify.sh", "init.sh",
                 "Dockerfile", "docker-compose.yml", "submission.json", "NOTES.md")
INCLUDE_DIRS = ("web", "mobile", "resource/dict", "data/inbox", "docs")
INCLUDE_GLOBS = ("*.py", "kb/*.py", "backup/*.py")


def _included(rel: str) -> bool:
    if rel.startswith(("models/", "data/raw/", "data/clean/", "data/backup/", "logs/", "evidence/")):
        return False
    if ".venv" in rel.split("/"):
        return False
    if rel.endswith(".log") or rel.endswith(".pt") or rel.endswith(".tmp"):
        return False
    if rel == ".env":
        return False
    return True


def collect() -> list[Path]:
    files: list[Path] = []
    for pattern in INCLUDE_GLOBS:
        for path in config.ROOT.glob(pattern):
            if path.is_file() and _included(str(path.relative_to(config.ROOT)).replace("\\", "/")):
                files.append(path)
    for name in INCLUDE_FILES:
        path = config.ROOT / name
        if path.exists():
            files.append(path)
    for name in INCLUDE_DIRS:
        base = config.ROOT / name
        if base.exists():
            files.extend(p for p in base.rglob("*") if p.is_file())
    for extra in ("data/question_bank.json", "data/corpus/entity_table.json",
                  "data/kb_index.json", "data/qbank_regression.json",
                  "schema.py", "config.py"):
        path = config.ROOT / extra
        if path.exists():
            files.append(path)
    files.append(make_log_digest())
    unique: dict[str, Path] = {str(p): p for p in files}
    return sorted(unique.values())


def make_log_digest() -> Path:
    """生成脱敏日志摘要 data/backup/logs_digest.jsonl：只保留渠道/时间/意图/状态等字段，
    不保留用户问句原文，随备份包一并归档（原文 qa_logs.jsonl 不入库不入包）。"""
    digest_path = config.BACKUP_DIR / "logs_digest.jsonl"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    qa_log = config.DATA_DIR / "qa_logs.jsonl"
    lines = []
    if qa_log.exists():
        with qa_log.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                lines.append({
                    "ts": rec.get("ts") or rec.get("at"),
                    "status": rec.get("status"),
                    "intent": rec.get("intent"),
                    "entity": rec.get("entity"),
                    "conf": rec.get("confidence"),
                })
    with digest_path.open("w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return digest_path


def make_tar(files: list[Path]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = config.BACKUP_DIR / f"backup-{stamp}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for path in files:
            tar.add(path, arcname=str(path.relative_to(config.ROOT)).replace("\\", "/"))
    keep = sorted(config.BACKUP_DIR.glob("backup-*.tar.gz"))
    for old in keep[:-config.BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return target


def _git_push(package: Path) -> dict:
    workdir = config.BACKUP_WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)
    if not config.BACKUP_REPO:
        return {"status": "skipped", "reason": "BACKUP_REPO 未配置，未推送"}
    if not (workdir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
        subprocess.run(["git", "remote", "add", "origin", config.BACKUP_REPO], cwd=workdir, check=True)
    elif subprocess.run(["git", "remote"], cwd=workdir, capture_output=True, text=True
                        ).stdout.strip() == "":
        subprocess.run(["git", "remote", "add", "origin", config.BACKUP_REPO], cwd=workdir, check=True)
    # 同一 remote 变更时更新；worktree 配置 git 身份（隔离，不影响全局）
    current = subprocess.run(["git", "remote", "get-url", "origin"], cwd=workdir,
                             capture_output=True, text=True).stdout.strip()
    if current != config.BACKUP_REPO:
        subprocess.run(["git", "remote", "set-url", "origin", config.BACKUP_REPO], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.name", "medqa-backup"], cwd=workdir, check=True)
    subprocess.run(["git", "config", "user.email", "medqa@local"], cwd=workdir, check=True)
    for path in collect():
        rel = path.relative_to(config.ROOT)
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    shutil.copy2(package, workdir / package.name)
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=True)
    commit = subprocess.run(["git", "commit", "-q", "-m", f"backup: {now_iso()}"],
                            cwd=workdir, capture_output=True, text=True)
    if commit.returncode != 0:
        # 无变更时 git commit 退出码非 0：回退为当前 HEAD
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workdir,
                              capture_output=True, text=True)
        if head.returncode == 0:
            return {"status": "pushed", "commit": head.stdout.strip(), "note": "no changes"}
        return {"status": "git_failed", "error": commit.stderr.strip()[:300]}
    subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=workdir, check=True)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workdir,
                          capture_output=True, text=True).stdout.strip()
    return {"status": "pushed", "commit": head, "commit_message": commit.stdout.strip()}


def run_backup() -> dict:
    config.ensure_dirs()
    files = collect()
    hits = scan_files(files)
    started = now_iso()
    if hits:
        with (config.LOG_DIR / "backup-alert.log").open("a", encoding="utf-8") as fh:
            for hit in hits[:20]:
                fh.write(f"{started}\t{hit['rule']}\t{hit['file']}\t{hit['sample']}\n")
        result = {"status": "blocked", "hits": len(hits), "detail": hits[:10],
                  "scanned": len(files), "at": started}
        log_line("backup", "blocked by sensitive scan", hits=len(hits))
        write_json(config.BACKUP_STATE_PATH, result)
        return result

    package = make_tar(files)
    report = {"created_at": started, "scanned": len(files), "hits": 0,
              "package": package.name, "files": [str(p.relative_to(config.ROOT)) for p in files]}
    write_json(config.BACKUP_DIR / f"{package.stem}-scan.json", report)

    if config.BACKUP_DRY_RUN:
        result = {"status": "dry_run", "package": package.name, "package_path": str(package),
                  "scanned": len(files), "hits": 0, "at": started,
                  "note": "BACKUP_DRY_RUN=true，仅打包与扫描，不推送"}
        write_json(config.BACKUP_STATE_PATH, result)
        log_line("backup", "dry run finished", package=package.name, files=len(files))
        return result

    try:
        pushed = _git_push(package)
    except subprocess.CalledProcessError as exc:
        pushed = {"status": "git_failed", "error": str(exc)}
    result = {"status": pushed.get("status", "pushed"), "package": package.name,
              "scanned": len(files), "hits": 0, "at": started, **pushed}
    write_json(config.BACKUP_STATE_PATH, result)
    log_line("backup", "finished", **{k: v for k, v in result.items() if k != "at"})
    return result
