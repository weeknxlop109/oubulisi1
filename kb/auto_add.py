"""知识库自动添加：目录监听 / 接口触发 / 定时同步共用同一条流水线。

扫描 -> 内容哈希去重 -> 解析 -> 清洗 -> 抽取三元组 -> 写 raw/clean
-> MERGE 入图 -> 自动生成问句 -> 写日志。幂等：doc_id = sha256(相对路径 + 内容)。
"""
from pathlib import Path

import config
import schema
from build_graph import build_from_records
from graph_store import GraphStore
from parser import iter_files, parse_and_clean, to_triples
from segment import build_dict
from utils import append_jsonl, log_line, now_iso, read_json, sha256_bytes, write_json


def _index() -> dict:
    data = read_json(config.KB_INDEX_PATH, None)
    if not isinstance(data, dict):
        data = {"docs": {}, "urls": {}, "updated_at": now_iso()}
    data.setdefault("docs", {})
    data.setdefault("urls", {})
    return data


def doc_id_for(path: Path, inbox: Path) -> str:
    rel = str(Path(path).relative_to(inbox)).replace("\\", "/")
    return sha256_bytes(rel.encode("utf-8"), Path(path).read_bytes())


def _store_raw(doc_id: str, record: dict, index: dict) -> bool:
    """原始集合永久保留：同一 url 只保留首份原文，不覆盖。"""
    url = record.get("url") or ""
    if url and url in index["urls"] and index["urls"][url] != doc_id:
        return False
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = config.RAW_DIR / f"{doc_id}.json"
    if not target.exists():
        write_json(target, record)
    if url:
        index["urls"].setdefault(url, doc_id)
    return True


def sync_once(inbox: Path | None = None) -> dict:
    config.ensure_dirs()
    inbox = Path(inbox or config.INBOX_DIR)
    index = _index()
    stats = {"scanned": 0, "added": 0, "skipped": 0, "pending": 0, "questions": 0,
             "triples": 0, "nodes_before": 0, "nodes_after": 0}
    graph = GraphStore()
    stats["nodes_before"] = graph.stats()["nodes"]
    pending_items: list[dict] = []

    for path in iter_files(inbox):
        stats["scanned"] += 1
        doc_id = doc_id_for(path, inbox)
        if doc_id in index["docs"]:
            stats["skipped"] += 1
            continue
        record = parse_and_clean(path)
        record["doc_id"] = doc_id
        triples = to_triples(record)
        _store_raw(doc_id, record, index)
        write_json(config.CLEAN_DIR / f"{doc_id}.json", record)
        append_jsonl(config.CORPUS_DIR / "clean.jsonl", record)

        if config.KB_REQUIRE_REVIEW:
            pending_items.append({"doc_id": doc_id, "record": record, "triples": triples})
            index["docs"][doc_id] = {"file": record["file"], "name": record["name"],
                                     "status": "pending", "at": now_iso()}
            stats["pending"] += 1
            continue

        before = graph.stats()
        build_from_records([record], graph)
        after = graph.stats()
        index["docs"][doc_id] = {"file": record["file"], "name": record["name"],
                                 "status": "merged", "at": now_iso(),
                                 "triples": len(triples),
                                 "nodes": after["nodes"] - before["nodes"],
                                 "edges": after["edges"] - before["edges"]}
        stats["added"] += 1
        stats["triples"] += len(triples)

    if pending_items:
        write_json(config.DATA_DIR / "kb_pending.json", pending_items)

    if stats["added"]:
        graph.save()
        build_dict(graph.entities())
        from kb import question_bank
        items = question_bank.generate(graph)
        stats["questions"] = len(items)
        # 新入库实体同步进训练数据资产
        from build_graph import load_clean_records
        from assets import build_assets
        records = load_clean_records()
        build_assets(records, [
            {"subject": e["from"], "subject_label": e["from_label"], "relation": e["type"],
             "object": e["to"], "object_label": e["to_label"], **e.get("props", {})}
            for e in graph.edges.values() if e["type"] != schema.ALIAS_OF
        ])

    index["updated_at"] = now_iso()
    write_json(config.KB_INDEX_PATH, index)
    stats["nodes_after"] = graph.stats()["nodes"]
    log_line("kb-sync", "sync finished", **stats)
    return stats


if __name__ == "__main__":
    import json
    print(json.dumps(sync_once(), ensure_ascii=False, indent=2))
