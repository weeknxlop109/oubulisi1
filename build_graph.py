"""建图层：清洗记录 -> 图谱节点/关系，MERGE 去重，统一 Disease -> 目标实体。

重跑幂等：节点与关系按 (Label, name) 合并，重复运行数量不翻倍。
"""
from pathlib import Path

import config
import schema
from assets import build_assets
from graph_store import GraphStore
from segment import build_dict
from utils import log_line, read_json, write_json


def load_clean_records() -> list[dict]:
    records = []
    for path in sorted(config.CLEAN_DIR.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict) and record.get("name"):
            records.append(record)
    return records


def _edge_props(record: dict) -> dict:
    return {"source": record.get("source", ""), "url": record.get("url", ""), "date": record.get("date", "")}


def build_from_records(records: list[dict], graph: GraphStore | None = None) -> dict:
    graph = graph or GraphStore()
    before = graph.stats()
    stats = {"records": len(records), "nodes_created": 0, "edges_created": 0,
             "skipped_empty": 0, "edges_existing": 0, "triples": 0}

    for record in records:
        name = record.get("name")
        if not name:
            stats["skipped_empty"] += 1
            continue
        if_graph = graph.merge_node(schema.DISEASE, name, {
            "desc": record.get("desc", ""), "type": record.get("type", ""),
            "source": record.get("source", ""), "url": record.get("url", ""),
            "cycle": record.get("cycle", ""), "plan": record.get("plan", ""),
        })
        if_graph["props"].setdefault("source", record.get("source", ""))

        for field, label in schema.FIELD_TO_LABEL.items():
            relation = schema.FIELD_TO_RELATION[field]
            items = record.get(field) or []
            if not items:
                stats["skipped_empty"] += 1
                continue
            for item in items:
                graph.merge_node(label, item, {"source": record.get("source", "")})
                created = graph.merge_relation(schema.DISEASE, name, relation, label, item, _edge_props(record))
                stats["triples"] += 1
                if created:
                    stats["edges_created"] += 1
                else:
                    stats["edges_existing"] += 1

        for alias in record.get("alias") or []:
            graph.merge_node(schema.DISEASE, alias, {"alias_only": True, "source": record.get("source", "")})
            created = graph.merge_relation(schema.DISEASE, name, schema.ALIAS_OF, schema.DISEASE, alias)
            if created:
                stats["edges_created"] += 1

    graph.save()
    after = graph.stats()
    stats["nodes_created"] = after["nodes"] - before["nodes"]
    stats["nodes_total"] = after["nodes"]
    stats["edges_total"] = after["edges"]
    stats["stats"] = after
    log_line("build_graph", "build finished", **{k: v for k, v in stats.items() if k != "stats"})
    return stats


def rebuild() -> dict:
    records = load_clean_records()
    graph = GraphStore()
    graph.clear()
    result = build_from_records(records, graph)
    payload = build_assets(records, [
        {"subject": e["from"], "subject_label": e["from_label"], "relation": e["type"],
         "object": e["to"], "object_label": e["to_label"], **e.get("props", {})}
        for e in graph.edges.values() if e["type"] != schema.ALIAS_OF
    ])
    build_dict(graph.entities())
    write_json(config.CORPUS_DIR / "graph_snapshot.json", {"stats": graph.stats(),
                                                           "nodes": list(graph.nodes.values()),
                                                           "edges": list(graph.edges.values())})
    result["entity_dict"] = str(config.DICT_PATH)
    result["entities"] = len(payload["entities"])
    return result


if __name__ == "__main__":
    print(rebuild())
