"""参数化 Cypher 子集执行器。

模板本身就是 Cypher（`$name` 为参数），执行器只解析本工程用到的
`MATCH (a:Label {name:$name})-[:REL]->(b:Label) RETURN b.name AS name` 与 `RETURN 1` 两种形态。
换成真实 Neo4j 时只需把 execute() 换成 driver 调用，模板与上层代码零改动。
"""
import re

from graph_store import GraphStore
import schema

_MATCH_RE = re.compile(
    r"^\s*MATCH\s*\(\s*(?P<a>[A-Za-z]+)\s*:\s*(?P<label_a>\w+)\s*\{\s*name\s*:\s*\$name\s*\}\s*\)"
    r"\s*-\s*\[\s*:\s*(?P<rel>\w+)\s*\]\s*->\s*"
    r"\(\s*(?P<b>[A-Za-z]+)\s*:\s*(?P<label_b>\w+)\s*\)\s*"
    r"RETURN\s+(?P<b2>[A-Za-z]+)\.name\s+AS\s+name\s*(?P<order>ORDER BY name)?\s*;?\s*$",
    re.IGNORECASE,
)

CYPHER_TEMPLATES = {
    schema.INTENT_DRUG:
        "MATCH (d:Disease {name:$name})-[:HAS_DRUG]->(m:Drug) RETURN m.name AS name ORDER BY name",
    schema.INTENT_SYMPTOM:
        "MATCH (d:Disease {name:$name})-[:HAS_SYMPTOM]->(m:Symptom) RETURN m.name AS name ORDER BY name",
    schema.INTENT_CHECK:
        "MATCH (d:Disease {name:$name})-[:NEED_CHECK]->(m:Check) RETURN m.name AS name ORDER BY name",
    schema.INTENT_PREVENT:
        "MATCH (d:Disease {name:$name})-[:PREVENTS]->(m:Prevent) RETURN m.name AS name ORDER BY name",
}

CONSTRAINTS = tuple(
    f"CREATE CONSTRAINT {label.lower()}_name IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.name IS UNIQUE"
    for label in schema.LABELS
)


class CypherError(ValueError):
    pass


def execute(graph: GraphStore, cypher: str, params: dict | None = None) -> dict:
    params = params or {}
    text = (cypher or "").strip()
    if re.fullmatch(r"(?i)RETURN\s+1\s+AS\s+ok\s*;?", text):
        return {"cypher": text, "params": params, "rows": [{"ok": 1}], "hit": True}

    m = _MATCH_RE.match(text)
    if not m:
        raise CypherError("不支持的 Cypher 形态（仅实现参数化单跳 MATCH）")
    name = params.get("name")
    if not name:
        raise CypherError("缺少参数 name")
    resolved = graph.resolve(name)
    if not resolved:
        return {"cypher": text, "params": params, "rows": [], "hit": False}
    label, canonical = resolved
    if label != m.group("label_a"):
        return {"cypher": text, "params": params, "rows": [], "hit": False, "resolved": canonical}
    nodes = graph.neighbors(label, canonical, m.group("rel"))
    rows = [{"name": n["name"]} for n in nodes if n["label"] == m.group("label_b")]
    if m.group("order"):
        rows.sort(key=lambda r: r["name"])
    return {"cypher": text, "params": params, "rows": rows, "hit": bool(rows), "resolved": canonical}


def run_intent(graph: GraphStore, intent: str, name: str) -> dict:
    template = CYPHER_TEMPLATES.get(intent)
    if not template:
        raise CypherError(f"intent 未定义查询模板：{intent}")
    result = execute(graph, template, {"name": name})
    from utils import log_line
    log_line("query", "executed", intent=intent, params=result["params"],
             rows=len(result["rows"]), hit=result["hit"], cypher=result["cypher"])
    return result


def evidence_path(name: str, intent: str, rows: list[dict]) -> list[str]:
    relation = schema.INTENT_SPECS[intent].relation
    obj_label = schema.RELATION_TO_LABEL.get(relation, "")
    return [
        f"(:{schema.DISEASE} {{{schema.PROP_NAME}:'{name}'}})-[:{relation}]->"
        f"(:{obj_label} {{name:'{row['name']}'}})"
        for row in rows
    ]
