"""配置中心：所有模块只从这里读取连接与路径，禁止在代码里散落常量。"""
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _path(env_key: str, default: Path) -> Path:
    raw = os.getenv(env_key)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


DATA_DIR = _path("DATA_DIR", ROOT / "data")
INBOX_DIR = DATA_DIR / "inbox"
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
CORPUS_DIR = DATA_DIR / "corpus"
BACKUP_DIR = DATA_DIR / "backup"
LOG_DIR = _path("LOG_DIR", ROOT / "logs")

GRAPH_PATH = DATA_DIR / "graph.json"
SESSION_PATH = DATA_DIR / "sessions.json"
QBANK_PATH = DATA_DIR / "question_bank.json"
KB_INDEX_PATH = DATA_DIR / "kb_index.json"
BACKUP_STATE_PATH = BACKUP_DIR / "backup_state.json"

MODEL_PATH = _path("MODEL_PATH", ROOT / "models" / "medical_ner_cls" / "best_model.pt")
MODEL_DIR = MODEL_PATH.parent
CURVE_PATH = MODEL_DIR / "curves.png"
METRICS_PATH = MODEL_DIR / "metrics.json"
DICT_PATH = _path("DICT_PATH", ROOT / "resource" / "dict" / "entity_dict.json")

WEB_DIR = ROOT / "web"
EVIDENCE_DIR = ROOT / "evidence"
REQUIREMENTS_PATH = ROOT / "requirements.txt"

# 存储实现说明：验收机未安装 pymongo / neo4j 驱动，且不允许新增依赖，
# 因此原始/清洗/题库/日志集合用本地文档文件实现，图存储用本地 JSON + 参数化 Cypher 子集执行器。
STORE_MODE = os.getenv("STORE_MODE", "local")
CONF_THRESHOLD = float(os.getenv("CONF_THRESHOLD", "0.75"))
NER_CONF_TARGET = float(os.getenv("NER_CONF_TARGET", "0.94"))
INTENT_CONF_TARGET = float(os.getenv("INTENT_CONF_TARGET", "0.96"))

KB_SYNC_INTERVAL_MIN = int(os.getenv("KB_SYNC_INTERVAL_MIN", "10"))
KB_REQUIRE_REVIEW = os.getenv("KB_REQUIRE_REVIEW", "false").lower() == "true"

BACKUP_CRON_HOUR = int(os.getenv("BACKUP_CRON_HOUR", "2"))
BACKUP_DRY_RUN = os.getenv("BACKUP_DRY_RUN", "true").lower() == "true"
BACKUP_REPO = os.getenv("BACKUP_REPO", "")
BACKUP_WORKDIR = BACKUP_DIR / "worktree"
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "30"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

QB_PASS_TARGET = float(os.getenv("QB_PASS_TARGET", "0.95"))
CYPHER_HIT_TARGET = float(os.getenv("CYPHER_HIT_TARGET", "0.90"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, INBOX_DIR, RAW_DIR, CLEAN_DIR, CORPUS_DIR, BACKUP_DIR,
              LOG_DIR, MODEL_DIR, DICT_PATH.parent, EVIDENCE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def snapshot() -> dict:
    """返回当前配置快照，供 /health 与 verify 展示。"""
    return {
        "store_mode": STORE_MODE,
        "conf_threshold": CONF_THRESHOLD,
        "model_path": str(MODEL_PATH),
        "dict_path": str(DICT_PATH),
        "graph_path": str(GRAPH_PATH),
        "kb_require_review": KB_REQUIRE_REVIEW,
        "backup_dry_run": BACKUP_DRY_RUN,
    }
