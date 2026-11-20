"""解析层：把 .md/.txt/.html/.pdf 解析成六字段 record，并保留可追溯来源字段。"""
from pathlib import Path
import re

from clean import clean_text, normalize_list
import config
import schema
from utils import now_iso

_FIELD_ALIASES = {
    "名称": "name", "疾病": "name", "病名": "name", "name": "name", "title": "name",
    "类型": "type", "分类": "type", "type": "type", "科室": "type",
    "别名": "alias", "又称": "alias", "alias": "alias",
    "描述": "desc", "简介": "desc", "概述": "desc", "定义": "desc", "desc": "desc",
    "症状": "symptom", "症状表现": "symptom", "symptom": "symptom",
    "常用药物": "drug", "药物": "drug", "用药": "drug", "推荐药物": "drug", "drug": "drug",
    "检查": "check", "相关检查": "check", "检查项目": "check", "check": "check",
    "预防": "prevent", "预防措施": "prevent", "prevent": "prevent",
    "治疗周期": "cycle", "疗程": "cycle", "cycle": "cycle",
    "治疗方案": "plan", "治疗": "plan", "plan": "plan",
    "来源": "source", "出处": "source", "source": "source",
    "url": "url", "链接": "url", "网址": "url",
}
_KV_RE = re.compile(r"^\s*(?:[-*+]\s*)?(?P<key>[^:：]{1,12})\s*[:：]\s*(?P<val>.+?)\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(?P<title>.+?)\s*$")
_LIST_FIELDS = {"symptom", "drug", "check", "prevent", "alias"}


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:  # 无 pypdf 时跳过，不引入新依赖
            return ""
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix in (".html", ".htm"):
        return clean_text(path.read_text(encoding="utf-8", errors="ignore"))
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_text(text: str, fallback_name: str) -> dict:
    record = {field: "" for field in schema.RECORD_FIELDS}
    record.update({"type": "", "alias": [], "cycle": "", "plan": "",
                   "source": "", "url": "", "extra": {}})
    heading = ""
    body_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m_head = _HEADING_RE.match(line)
        if m_head:
            heading = clean_text(m_head.group("title"))
            continue
        m_kv = _KV_RE.match(line)
        if m_kv:
            key = _FIELD_ALIASES.get(clean_text(m_kv.group("key")).lower())
            val = clean_text(m_kv.group("val"))
            if key in _LIST_FIELDS:
                record[key] = normalize_list(val)
            elif key:
                record[key] = val
            else:
                body_lines.append(line)
            continue
        body_lines.append(line)

    if not record["name"]:
        record["name"] = heading or fallback_name
    if not record["desc"]:
        record["desc"] = clean_text(" ".join(body_lines))[:300]
    for field in ("name", "desc", "type", "cycle", "plan", "source", "url"):
        record[field] = clean_text(record[field])
    return record


def parse_and_clean(path: Path) -> dict:
    text = read_document(Path(path))
    record = parse_text(text, Path(path).stem)
    record["path"] = str(Path(path))
    record["file"] = Path(path).name
    record["source"] = record["source"] or f"inbox/{Path(path).name}"
    record["url"] = record["url"] or f"file://{Path(path).as_posix()}"
    record["date"] = now_iso()
    record["raw_text"] = clean_text(text)[:4000]
    record["inbox_dir"] = config.INBOX_DIR.name
    return record


def to_triples(record: dict) -> list[dict]:
    """record -> 三元组列表，方向统一 Disease -> 目标实体。"""
    name = record.get("name")
    if not name:
        return []
    triples: list[dict] = []
    for field, label in schema.FIELD_TO_LABEL.items():
        relation = schema.FIELD_TO_RELATION[field]
        for item in record.get(field) or []:
            triples.append({
                "subject": name,
                "subject_label": schema.DISEASE,
                "relation": relation,
                "object": item,
                "object_label": label,
                "source": record.get("source", ""),
                "url": record.get("url", ""),
                "date": record.get("date", ""),
            })
    for alias in record.get("alias") or []:
        triples.append({
            "subject": name,
            "subject_label": schema.DISEASE,
            "relation": schema.ALIAS_OF,
            "object": alias,
            "object_label": schema.DISEASE,
            "source": record.get("source", ""),
            "url": record.get("url", ""),
            "date": record.get("date", ""),
        })
    return triples


def iter_files(root: Path):
    root = Path(root)
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".md", ".txt", ".html", ".htm", ".pdf", ".json"):
            yield path
