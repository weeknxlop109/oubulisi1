"""分词层：双向最大匹配 + 启发式选择。

前向从左到右、后向从右到左各切一次，窗口从最长词长递减；
启发式：词数更少优先 -> 单字词更少优先 -> 相同则取后向结果（医学专有词更易保持完整）。
"""
from pathlib import Path

import config
import schema
from utils import read_json

# 非实体通用词，补足问句骨架，避免把「吃什么药」切碎
BASE_WORDS = (
    "什么", "怎么", "如何", "哪些", "哪个", "需要", "可以", "能不能", "会不会",
    "有没有", "治疗", "药物", "药品", "用药", "症状", "检查", "预防", "注意",
    "并发症", "相互", "作用", "一起", "周期", "多久", "时间", "方案", "表现",
    "可能", "得了", "患有", "引起", "导致", "出现", "推荐", "常用", "确诊",
    "判断", "办法", "措施", "办法", "该", "吃", "用", "查", "做", "有", "是",
    "的", "了", "吗", "呢", "和", "与", "及", "要", "能", "会", "还", "后",
)

_WORDS_CACHE: dict[str, int] | None = None


def _dict_words() -> dict[str, int]:
    global _WORDS_CACHE
    if _WORDS_CACHE is not None:
        return _WORDS_CACHE
    words = {w: 1 for w in BASE_WORDS}
    data = read_json(config.DICT_PATH, {})
    for ent in data.get("entities", []):
        name = ent.get("name", "")
        if name:
            words[name] = 1
        for alias in ent.get("aliases", []) or []:
            words[alias] = 1
    for w in data.get("words", []) or []:
        words[w] = 1
    _WORDS_CACHE = words
    return words


def reload_dict() -> None:
    global _WORDS_CACHE
    _WORDS_CACHE = None
    _dict_words()


def max_word_len() -> int:
    return max((len(w) for w in _dict_words()), default=1)


def _forward(text: str) -> list[str]:
    words = _dict_words()
    out, i, n = [], 0, len(text)
    max_len = max_word_len()
    while i < n:
        for size in range(min(max_len, n - i), 1, -1):
            piece = text[i:i + size]
            if piece in words:
                out.append(piece)
                i += size
                break
        else:
            out.append(text[i])
            i += 1
    return out


def _backward(text: str) -> list[str]:
    words = _dict_words()
    out, i = [], len(text)
    max_len = max_word_len()
    while i > 0:
        for size in range(min(max_len, i), 1, -1):
            piece = text[i - size:i]
            if piece in words:
                out.append(piece)
                i -= size
                break
        else:
            out.append(text[i - 1])
            i -= 1
    return list(reversed(out))


def _single_char_count(tokens: list[str]) -> int:
    return sum(1 for t in tokens if len(t) == 1)


def cut(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    fwd, bwd = _forward(text), _backward(text)
    if len(fwd) != len(bwd):
        return fwd if len(fwd) < len(bwd) else bwd
    if _single_char_count(fwd) != _single_char_count(bwd):
        return fwd if _single_char_count(fwd) < _single_char_count(bwd) else bwd
    return bwd


def build_dict(entities: list[dict]) -> dict:
    """实体词典写入 resource/dict/entity_dict.json，供分词与实体识别复用。"""
    payload = {
        "version": 1,
        "labels": list(schema.LABELS),
        "entities": sorted(entities, key=lambda e: (e.get("label", ""), e.get("name", ""))),
        "words": sorted(set(BASE_WORDS)),
    }
    config.DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    from utils import write_json
    write_json(Path(config.DICT_PATH), payload)
    reload_dict()
    return payload


if __name__ == "__main__":
    for sample in ("感冒药物治疗", "风热感冒吃什么药", "高血压需要做什么检查"):
        print(sample, "->", "/".join(cut(sample)))
