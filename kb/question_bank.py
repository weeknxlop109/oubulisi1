"""题库：从图谱自动生成问句与标准答案，并做回归验证。

覆盖 9 类问句：4 类第一版实现并纳入通过率统计，另 5 类显式标注「待扩展」并给出扩展路径，
不得静默缺失（对应验收判据 A-V2）。
"""
import itertools

import config
import schema
from cypher_engine import run_intent
from graph_store import GraphStore, load_graph
from utils import log_line, now_iso, read_json, write_json

TEMPLATES = {
    schema.INTENT_DRUG: ["{d}吃什么药", "{d}用什么药好", "治疗{d}的药物有哪些", "得了{d}可以吃什么药"],
    schema.INTENT_SYMPTOM: ["{d}有哪些症状", "{d}的症状表现是什么", "怎么判断是不是{d}", "{d}一般有什么表现"],
    schema.INTENT_CHECK: ["{d}需要做什么检查", "确诊{d}要查什么", "{d}的相关检查有哪些"],
    schema.INTENT_PREVENT: ["怎么预防{d}", "{d}如何预防", "{d}的预防措施有哪些"],
}

PENDING_TEMPLATES = {
    schema.INTENT_PLAN: ["{d}怎么治疗", "{d}的治疗方案是什么"],
    schema.INTENT_CYCLE: ["{d}治疗需要多久", "{d}的治疗周期多长"],
    schema.INTENT_COMPLICATION: ["{d}会引起哪些并发症"],
    schema.INTENT_INTERACTION: ["{a}和{b}能一起吃吗", "{a}与{b}有相互作用吗"],
    schema.INTENT_POSSIBLE_DISEASE: ["{s}可能是得了什么病", "出现{s}是什么病"],
}


def _diseases(graph: GraphStore) -> list[dict]:
    return [n for n in graph.nodes.values()
            if n["label"] == schema.DISEASE and not n["props"].get("alias_only")]


def generate(graph: GraphStore | None = None) -> list[dict]:
    graph = graph or load_graph()
    items: list[dict] = []
    seq = itertools.count(1)

    for disease in _diseases(graph):
        name = disease["name"]
        for intent, patterns in TEMPLATES.items():
            result = run_intent(graph, intent, name)
            expected = [row["name"] for row in result["rows"]]
            if not expected:
                continue
            for pattern in patterns:
                items.append({
                    "qid": f"Q{next(seq):04d}",
                    "question": pattern.format(d=name),
                    "intent": intent,
                    "entity": name,
                    "expected": expected,
                    "supported": True,
                    "relation": schema.INTENT_SPECS[intent].relation,
                })

    drugs = [n["name"] for n in graph.nodes.values() if n["label"] == schema.DRUG]
    symptoms = [n["name"] for n in graph.nodes.values() if n["label"] == schema.SYMPTOM]
    for disease in _diseases(graph):
        name = disease["name"]
        for intent in (schema.INTENT_PLAN, schema.INTENT_CYCLE, schema.INTENT_COMPLICATION):
            for pattern in PENDING_TEMPLATES[intent]:
                items.append({
                    "qid": f"Q{next(seq):04d}", "question": pattern.format(d=name),
                    "intent": intent, "entity": name, "expected": [], "supported": False,
                })

    for a, b in list(itertools.islice(itertools.combinations(drugs, 2), 3)):
        for pattern in PENDING_TEMPLATES[schema.INTENT_INTERACTION]:
            items.append({
                "qid": f"Q{next(seq):04d}", "question": pattern.format(a=a, b=b),
                "intent": schema.INTENT_INTERACTION, "entity": a,
                "expected": [], "supported": False,
            })

    for s in symptoms[:3]:
        for pattern in PENDING_TEMPLATES[schema.INTENT_POSSIBLE_DISEASE]:
            items.append({
                "qid": f"Q{next(seq):04d}", "question": pattern.format(s=s),
                "intent": schema.INTENT_POSSIBLE_DISEASE, "entity": s,
                "expected": [], "supported": False,
            })

    payload = {"generated_at": now_iso(), "total": len(items), "items": items}
    write_json(config.QBANK_PATH, payload)
    log_line("question_bank", "generated", total=len(items),
             supported=sum(1 for i in items if i["supported"]))
    return items


def load_items() -> list[dict]:
    data = read_json(config.QBANK_PATH, {})
    return data.get("items", [])


def coverage() -> dict:
    items = load_items()
    rows = []
    for intent in schema.INTENTS:
        spec = schema.INTENT_SPECS[intent]
        count = sum(1 for i in items if i["intent"] == intent)
        rows.append({
            "intent": intent, "name": schema.INTENT_CN[intent],
            "supported": spec.supported, "count": count,
            "extension": spec.extension,
            "status": "已实现" if spec.supported else "待扩展",
        })
    return {"total": len(items), "intents": rows}


def regress(limit: int | None = None) -> dict:
    """题库回归：逐条执行问答，通过率 = 命中标准答案且状态正常。"""
    from qa_service import answer_question

    items = [i for i in load_items() if i["supported"]]
    if limit:
        items = items[:limit]
    passed, failed = 0, []
    for item in items:
        result = answer_question(item["question"], use_context=False)
        hit = result.get("status") == "ok" and any(
            exp in (result.get("answer") or "") for exp in item["expected"])
        if hit:
            passed += 1
        else:
            failed.append({"qid": item["qid"], "question": item["question"],
                           "status": result.get("status"), "answer": result.get("answer"),
                           "intent": result.get("intent"), "entity": result.get("entity")})
    rate = round(passed / len(items), 4) if items else 0.0
    report = {"ran_at": now_iso(), "total": len(items), "passed": passed,
              "failed": len(failed), "pass_rate": rate,
              "target": config.QB_PASS_TARGET, "pass": rate >= config.QB_PASS_TARGET,
              "failures": failed[:20]}
    write_json(config.DATA_DIR / "qbank_regression.json", report)
    log_line("qbank_regression", "done", total=len(items), passed=passed, rate=rate)
    return report
