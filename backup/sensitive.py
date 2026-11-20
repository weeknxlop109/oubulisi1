"""敏感信息扫描：推送前强制步骤，命中即中止。"""
import re

PATTERNS = (
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")),
    ("github_pat", re.compile(r"\bgh[ps]_[A-Za-z0-9]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("password", re.compile(r"(?i)(password|passwd|pwd)\s*=\s*(?!\*{3,}|$)[^\s\"']{6,}")),
)


def scan_text(text: str, name: str = "<text>") -> list[dict]:
    hits = []
    for rule, pattern in PATTERNS:
        for match in pattern.finditer(text):
            hits.append({"rule": rule, "file": name, "sample": match.group(0)[:12] + "***"})
    return hits


def scan_files(files) -> list[dict]:
    hits: list[dict] = []
    for path in files:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(text, path.name))
    return hits
