"""在线问答链路：预处理 -> 实体识别 -> 意图分类 -> 问句解析 -> 参数化 Cypher -> 模板答案。

答案由模板生成，不调用大模型；结果为空、低置信度、高风险时走兜底。
每步输出中间产物（question / tokens / entity / intent / facts / answer），供逐层排查。
"""
import time
from pathlib import Path

import config
import ner_cls
import schema
import segment
from cypher_engine import CypherError, evidence_path, run_intent
from graph_store import load_graph
from utils import append_jsonl, log_line, now_iso, read_json, write_json

MEDICAL_CUES = ("药", "吃", "症状", "检查", "预防", "治疗", "病", "疼", "痛", "发热", "发烧",
                "感冒", "血压", "血糖", "鼻炎", "失眠", "胃炎", "咳嗽", "腹泻", "过敏", "炎症")

INTENT_RULES = (
    (schema.INTENT_INTERACTION, ("相互作用", "一起吃", "同服", "配伍", "相克", "一起用")),
    (schema.INTENT_COMPLICATION, ("并发症", "合并症")),
    (schema.INTENT_POSSIBLE_DISEASE, ("可能是", "是什么病", "得了什么病", "什么病会引起")),
    (schema.INTENT_CYCLE, ("周期", "多久", "多长时间", "几天", "疗程")),
    (schema.INTENT_PLAN, ("治疗方案", "怎么治", "如何治", "治疗方法", "怎么处理")),
    (schema.INTENT_CHECK, ("检查", "查什么", "查一下", "化验", "拍片")),
    (schema.INTENT_PREVENT, ("预防", "注意什么", "要注意", "避免", "防止")),
    (schema.INTENT_SYMPTOM, ("症状", "表现", "有哪些不适", "怎么判断")),
    (schema.INTENT_DRUG, ("吃什么药", "用什么药", "推荐药", "药物", "吃药", "用药", "能不能吃")),
)

TEMPLATE_ANSWER = {
    schema.INTENT_DRUG: "针对「{entity}」，知识图谱记录的常用药物包括：{names}。",
    schema.INTENT_SYMPTOM: "「{entity}」的常见症状表现包括：{names}。",
    schema.INTENT_CHECK: "「{entity}」通常需要做的检查包括：{names}。",
    schema.INTENT_PREVENT: "预防「{entity}」可参考以下措施：{names}。",
}


# ---------------- 基础工具 ----------------
_DICT_CACHE: dict = {"mtime": -1.0, "entities": []}


def _entities_from_dict() -> list[dict]:
    try:
        mtime = config.DICT_PATH.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _DICT_CACHE["mtime"] != mtime:
        data = read_json(config.DICT_PATH, {})
        _DICT_CACHE["entities"] = data.get("entities", []) or []
        _DICT_CACHE["mtime"] = mtime
    return _DICT_CACHE["entities"]


def _graph_lookup(name: str) -> dict | None:
    graph = load_graph()
    resolved = graph.resolve(name)
    if not resolved:
        return None
    label, canonical = resolved
    node = graph.node(label, canonical)
    if not node:
        return None
    return {"label": label, "name": canonical, "props": node.get("props", {})}


def _dict_spans(question: str, entities: list[dict]) -> list[dict]:
    candidates = []
    for ent in entities:
        for surface in [ent.get("name")] + list(ent.get("aliases") or []):
            if not surface:
                continue
            start = question.find(surface)
            while start != -1:
                candidates.append({"text": surface, "label": ent.get("label"),
                                   "canonical": ent.get("name"), "start": start,
                                   "end": start + len(surface)})
                start = question.find(surface, start + 1)
    candidates.sort(key=lambda c: (-(c["end"] - c["start"]), c["start"]))
    chosen: list[dict] = []
    for cand in candidates:
        if any(not (cand["end"] <= c["start"] or cand["start"] >= c["end"]) for c in chosen):
            continue
        chosen.append(cand)
    return sorted(chosen, key=lambda c: c["start"])


def _rule_intent(question: str) -> str | None:
    for intent, keywords in INTENT_RULES:
        if any(k in question for k in keywords):
            return intent
    return None


def _pick_entity(question: str, spans: list[dict], intent: str) -> dict | None:
    if not spans:
        return None
    spec = schema.INTENT_SPECS.get(intent)
    expected = spec.entity_label if spec else None
    if expected:
        for span in spans:
            if span.get("label") == expected:
                return span
    return spans[0]


# ---------------- 会话记忆 ----------------
def _load_sessions() -> dict:
    data = read_json(config.SESSION_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_sessions(sessions: dict) -> None:
    write_json(config.SESSION_PATH, sessions)


def remember(session_id: str, question: str, result: dict) -> None:
    if not session_id:
        return
    sessions = _load_sessions()
    turns = sessions.get(session_id, [])
    turns.append({"ts": now_iso(), "question": question, "entity": result.get("entity"),
                  "intent": result.get("intent"), "status": result.get("status")})
    sessions[session_id] = turns[-5:]
    if len(sessions) > 200:
        for key in list(sessions)[:-200]:
            sessions.pop(key, None)
    _save_sessions(sessions)


def recall(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    turns = _load_sessions().get(session_id, [])
    for turn in reversed(turns):
        if turn.get("entity"):
            return turn
    return None


# ---------------- 主链路 ----------------
def answer_question(question: str, session_id: str | None = None, use_context: bool = True) -> dict:
    t0 = time.perf_counter()
    text = (question or "").strip().replace("？", "?").replace("！", "!")
    trace: dict = {"question": text}
    timings: dict = {}

    # 1. 预处理
    t = time.perf_counter()
    if not text or len(text) < 2:
        log_line("preprocess", "empty or too short question", question=text)
        return _fallback("empty", "请输入完整的问题，例如「风热感冒吃什么药」。", trace, t0, entity=None)
    if len(text) > 120:
        log_line("preprocess", "question truncated", length=len(text))
        text = text[:120]
    tokens = segment.cut(text)
    trace["tokens"] = tokens
    timings["preprocess_ms"] = round((time.perf_counter() - t) * 1000, 2)
    log_line("preprocess", "preprocessed", question=text, tokens=len(tokens),
             has_dictionary=bool(segment._dict_words()))

    # 2. 安全兜底优先：高风险问句一律建议就医
    risky = [kw for kw in schema.HIGH_RISK_KEYWORDS if kw in text]
    if risky:
        log_line("safety", "high risk question", question=text, keywords=risky)
        result = _fallback("safety", "该情况可能涉及较高健康风险，建议尽快前往医院就诊或拨打急救电话，"
                                     "不要自行用药或拖延。", trace, t0, entity=None)
        result["risk_keywords"] = risky
        result["intent"] = "safety"
        result["intent_name"] = "高风险问句"
        result["trace"]["intent"] = "safety"
        remember(session_id, text, result) if use_context else None
        _write_qa_log(text, result, session_id)
        return result

    # 3. 实体识别 + 意图分类（模型为主，词典/规则兜底）
    t_model = time.perf_counter()
    predictor = ner_cls.load()
    entities = _entities_from_dict()
    dict_spans = _dict_spans(text, entities)
    model_ner = predictor.ner(text) if predictor else {"spans": [], "confidence": 0.0, "tags": [], "tokens": []}
    model_spans = [s for s in model_ner["spans"] if _graph_lookup(s["text"])]

    spans = dict_spans or model_spans
    if not spans:
        spans = model_spans
    if not spans and use_context and session_id:
        # 上下文改写：省略主语的追问沿用上一轮实体
        last = recall(session_id)
        if last and last.get("entity") and len(text) <= 20 and any(
                text.startswith(p) for p in ("那", "它", "这个", "该", "还要", "还有", "另外")):
            spans = [{"text": last["entity"], "label": _graph_lookup(last["entity"])["label"] if _graph_lookup(last["entity"]) else schema.DISEASE,
                      "canonical": last["entity"], "start": 0, "end": len(last["entity"]), "from_context": True}]
            trace["context_rewrite"] = f"{last['entity']} + {text}"

    model_intent = predictor.intent(text) if predictor else {"intent": "", "confidence": 0.0, "probs": {}}
    rule = _rule_intent(text)
    intent = rule or model_intent["intent"]
    intent_conf = model_intent["probs"].get(intent, 0.0) if model_intent["probs"] else 0.0
    if rule:
        intent_conf = max(intent_conf, 0.9)
    if not intent:
        intent = schema.INTENT_DRUG
    timings["model_ms"] = round((time.perf_counter() - t_model) * 1000, 2)

    entity = _pick_entity(text, spans, intent)
    if entity:
        lookup = _graph_lookup(entity["canonical"])
        if lookup:
            entity = {**entity, "canonical": lookup["name"], "label": lookup["label"]}
    ner_conf = model_ner["confidence"] if spans else 0.0
    trace["entity"] = entity["canonical"] if entity else None
    trace["entity_label"] = entity["label"] if entity else None
    trace["intent"] = intent
    trace["intent_confidence"] = round(intent_conf, 4)
    trace["ner_confidence"] = round(ner_conf, 4)
    trace["intent_source"] = "rule" if rule else "model"
    trace["spans"] = spans
    log_line("ner", "recognized" if spans else "miss", question=text,
             spans=spans, model_spans=model_ner["spans"], confidence=ner_conf)
    log_line("cls", "classified", question=text, intent=intent,
             confidence=round(intent_conf, 4), source=trace["intent_source"])

    base = {
        "entity": trace["entity"],
        "entity_label": trace["entity_label"],
        "intent": intent,
        "intent_name": schema.INTENT_CN.get(intent, intent),
        "confidence": {"ner": round(ner_conf, 4), "intent": round(intent_conf, 4)},
        "trace": trace,
        "timings": timings,
        "session_id": session_id,
    }

    if not entity:
        log_line("ner", "entity not recognized", question=text, tokens=tokens,
                 model_spans=model_ner["spans"])
        if any(cue in text for cue in MEDICAL_CUES):
            return _finish("clarify", "没有识别到具体的疾病或药品名称，请补充完整，例如「风热感冒吃什么药」。",
                           base, t0, session_id, text, use_context)
        return _finish("empty", "未查询到相关信息。本系统只回答医药知识图谱内的疾病、用药、检查与预防问题。",
                       base, t0, session_id, text, use_context)

    if intent_conf < config.CONF_THRESHOLD and schema.INTENT_SPECS[intent].supported:
        log_line("cls", "low intent confidence", question=text, intent=intent,
                 confidence=intent_conf)
        return _finish("clarify", "没能确定你要问的具体方面，请说明是询问用药、症状、检查还是预防。",
                       base, t0, session_id, text, use_context)

    spec = schema.INTENT_SPECS[intent]
    if not spec.supported:
        answer = (f"「{schema.INTENT_CN[intent]}」属于本系统 9 类问句中的待扩展项，"
                  f"当前版本未开放该查询。扩展路径：{spec.extension}。")
        base["status_detail"] = "pending"
        return _finish("pending", answer, base, t0, session_id, text, use_context)

    # 4. 参数化 Cypher 检索
    t_query = time.perf_counter()
    try:
        result = run_intent(load_graph(), intent, entity["canonical"])
    except CypherError as exc:
        log_line("query", f"cypher error: {exc}", question=text)
        timings["query_ms"] = round((time.perf_counter() - t_query) * 1000, 2)
        return _finish("empty", "查询模板执行失败，请检查图谱与查询模板配置。", base, t0, session_id, text, use_context)
    timings["query_ms"] = round((time.perf_counter() - t_query) * 1000, 2)

    if not result["rows"]:
        log_line("query", "no rows", question=text, cypher=result["cypher"],
                 params=result["params"], entity=entity["canonical"])
        return _finish("empty", f"图谱中暂未收录「{entity['canonical']}」的"
                                f"{schema.INTENT_CN[intent]}信息，未作推测。",
                       base, t0, session_id, text, use_context)

    names = "、".join(row["name"] for row in result["rows"])
    answer = TEMPLATE_ANSWER[intent].format(entity=entity["canonical"], names=names)
    node = _graph_lookup(entity["canonical"]) or {}
    props = node.get("props", {})
    base["facts"] = [row["name"] for row in result["rows"]]
    base["evidence"] = {
        "cypher": result["cypher"],
        "params": result["params"],
        "rows": result["rows"],
        "path": evidence_path(entity["canonical"], intent, result["rows"]),
        "source": props.get("source", ""),
        "url": props.get("url", ""),
    }
    base["trace"]["facts"] = base["facts"]
    base["trace"]["cypher"] = result["cypher"]
    return _finish("ok", answer, base, t0, session_id, text, use_context)


def _fallback(status: str, answer: str, trace: dict, t0: float, entity) -> dict:
    return {
        "status": status,
        "answer": answer,
        "entity": entity,
        "entity_label": None,
        "intent": "",
        "intent_name": "",
        "confidence": {"ner": 0.0, "intent": 0.0},
        "evidence": {},
        "timings": {"total_ms": round((time.perf_counter() - t0) * 1000, 2)},
        "trace": trace,
        "safety_note": schema.SAFETY_NOTE,
    }


def _finish(status: str, answer: str, base: dict, t0: float, session_id: str | None,
            question: str, use_context: bool) -> dict:
    base.update({
        "status": status,
        "answer": answer,
        "safety_note": schema.SAFETY_NOTE,
        "answered_at": now_iso(),
    })
    base.setdefault("evidence", {})
    timings = base.setdefault("timings", {})
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    base["trace"]["answer"] = answer
    base["trace"]["status"] = status
    if use_context:
        remember(session_id, question, base)
    _write_qa_log(question, base, session_id)
    return base


def _write_qa_log(question: str, result: dict, session_id: str | None) -> None:
    append_jsonl(config.DATA_DIR / "qa_logs.jsonl", {
        "ts": now_iso(), "session_id": session_id, "question": question,
        "status": result.get("status"), "entity": result.get("entity"),
        "intent": result.get("intent"), "answer": result.get("answer"),
        "confidence": result.get("confidence"), "timings": result.get("timings"),
    })


def demo_cases() -> list[dict]:
    return [
        {"type": "正常·用药", "question": "风热感冒吃什么药", "expect": "药品列表 + HAS_DRUG 证据 + 安全提示"},
        {"type": "正常·症状", "question": "感冒有哪些症状", "expect": "症状列表 + HAS_SYMPTOM 证据"},
        {"type": "正常·检查", "question": "感冒需要做什么检查", "expect": "检查项 + NEED_CHECK 证据"},
        {"type": "正常·预防", "question": "怎么预防感冒", "expect": "预防措施 + PREVENTS 证据"},
        {"type": "空结果", "question": "量子纠缠怎么影响股票", "expect": "拒答，不编造"},
        {"type": "低置信度", "question": "那个东西能不能吃", "expect": "请求补充信息"},
        {"type": "高风险", "question": "孕妇胸闷气短怎么办", "expect": "建议及时就医"},
        {"type": "待扩展", "question": "感冒怎么治疗", "expect": "标注待扩展并给出扩展路径"},
    ]


def warmup() -> None:
    """服务启动时预热模型与图谱，避免首问耗时抖动。"""
    ner_cls.load()
    load_graph()
    segment.cut("预热问句")


if __name__ == "__main__":
    import json

    for q in ("风热感冒吃什么药", "感冒有哪些症状", "感冒需要做什么检查", "怎么预防感冒",
              "量子纠缠怎么影响股票", "那个东西能不能吃", "孕妇胸闷气短怎么办", "感冒怎么治疗"):
        print("=" * 60)
        print(q)
        res = answer_question(q, use_context=False)
        print(json.dumps({k: res[k] for k in ("status", "entity", "intent", "answer",
                                             "confidence", "timings")},
                         ensure_ascii=False, indent=2))
