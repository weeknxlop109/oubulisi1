"""图谱体检与修复：重复节点、孤立节点、反向关系、空值/乱码四类问题。

修复顺序：回清洗层重新建图（消除重复/反向/空值），再移除无来源的孤立目标节点，
修复后重跑建图与题库回归。
"""
import re

import config
import schema
from graph_store import GraphStore, load_graph
from utils import append_jsonl, log_line, now_iso

_GARBLE_RE = re.compile(r"[\ufffd]|<\?|&[a-z]+;", re.IGNORECASE)
DATA_FIX_LOG = config.LOG_DIR / "graph-fix.jsonl"


def check(graph: GraphStore | None = None) -> dict:
    graph = graph or load_graph()
    norm: dict[tuple[str, str], list[str]] = {}
    for node in graph.nodes.values():
        key = (node["label"], re.sub(r"\s+", "", node["name"]).lower())
        norm.setdefault(key, []).append(node["name"])
    duplicates = [names for names in norm.values() if len(names) > 1]

    connected = set()
    for edge in graph.edges.values():
        connected.add((edge["from_label"], edge["from"]))
        connected.add((edge["to_label"], edge["to"]))
    isolated = [n["name"] for n in graph.nodes.values()
                if (n["label"], n["name"]) not in connected]

    reversed_edges = [f"{e['from_label']}:{e['from']} -[{e['type']}]-> {e['to']}"
                      for e in graph.edges.values()
                      if e["type"] != schema.ALIAS_OF and e["from_label"] != schema.DISEASE]

    bad_values = []
    for node in graph.nodes.values():
        if not node["name"].strip() or _GARBLE_RE.search(node["name"]) or len(node["name"]) > 30:
            bad_values.append(f"{node['label']}::{node['name']}")
        for key, value in (node.get("props") or {}).items():
            if isinstance(value, str) and _GARBLE_RE.search(value):
                bad_values.append(f"{node['label']}::{node['name']}.{key}")

    missing_props = [n["name"] for n in graph.nodes.values()
                     if n["label"] == schema.DISEASE and not n["props"].get("alias_only")
                     and not n["props"].get("desc")]

    return {
        "checked_at": now_iso(),
        "nodes": len(graph.nodes), "edges": len(graph.edges),
        "duplicates": duplicates,
        "isolated": isolated,
        "reversed": reversed_edges,
        "bad_values": bad_values,
        "missing_desc": missing_props,
        "ok": not (duplicates or isolated or reversed_edges or bad_values),
    }


def repair() -> dict:
    from build_graph import rebuild

    config_before = check()
    rebuild()
    graph = load_graph()
    removed = []
    for node in list(graph.nodes.values()):
        if node["label"] == schema.DISEASE or node["props"].get("alias_only"):
            continue
        key = (node["label"], node["name"])
        used = any((e["from_label"], e["from"]) == key or (e["to_label"], e["to"]) == key
                   for e in graph.edges.values())
        if not used:
            graph.nodes.pop(graph._node_key(*key), None)
            removed.append(f"{key[0]}::{key[1]}")
    if removed:
        graph.save()
        from segment import build_dict
        build_dict(graph.entities())
    after = check(graph)
    actions = {
        "duplicates": "按 Label+name 重新 MERGE 合并",
        "isolated": "移除无来源的孤立目标节点" if removed else "无孤立节点",
        "reversed": "按 Disease -> 目标实体重建关系",
        "bad_values": "回清洗层重新标准化",
    }
    record = {"at": now_iso(), "before": config_before, "after": after,
              "removed_isolated": removed, "actions": actions}
    changed = {k: config_before[k] for k in config_before if k != "checked_at"} != \
              {k: after[k] for k in after if k != "checked_at"}
    if changed:
        append_jsonl(DATA_FIX_LOG, record)
    log_line("graph-fix", "repair finished", before_ok=config_before["ok"], after_ok=after["ok"],
             removed=len(removed))
    return record


if __name__ == "__main__":
    import json
    print(json.dumps(check(), ensure_ascii=False, indent=2))
