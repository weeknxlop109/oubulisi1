"""清洗层：去 HTML、空白归一、全角转半角、列表切分与保序去重。

医学语义保护：否定词（不/无/未/否/忌）、时间、禁忌、剂量数字一律保留，不做同义替换。
"""
import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_SPLIT_RE = re.compile(r"[、,，;；/｜|\n]+")

# 需要保留的否定/禁忌/时间/剂量表达（仅用于自检，不参与删除）
PROTECT_PATTERNS = (
    re.compile(r"(不|无|未|否|忌|避免|慎用)"),
    re.compile(r"\d+\s*(mg|g|ml|片|粒|次|天|周|日|小时|分钟)", re.IGNORECASE),
)


def to_halfwidth(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out)


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = _ENTITY_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = to_halfwidth(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n", text)
    return text.strip(" \n\t\u3000·-—")


def normalize_list(value) -> list[str]:
    """切分 -> 逐项清洗 -> 去空 -> 保序去重。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(v) for v in value]
    else:
        raw_items = _SPLIT_RE.split(str(value))
    seen: set[str] = set()
    out: list[str] = []
    for item in raw_items:
        item = clean_text(item)
        item = item.strip("。.;；")
        if not item or len(item) > 40:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def protected_terms(text: str) -> list[str]:
    """返回文本里命中的保护性表达，用于入图前抽样核对。"""
    hits: list[str] = []
    for pattern in PROTECT_PATTERNS:
        hits.extend(m.group(0) for m in pattern.finditer(text or ""))
    return hits
