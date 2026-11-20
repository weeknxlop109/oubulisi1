"""本地图存储：按 Label + name 去重，等价于 Neo4j 的唯一约束 + MERGE。

验收机没有 neo4j 驱动且不允许新增依赖，图数据落盘为 data/graph.json，
对外仍以「节点 / 关系 / 方向」的图语义暴露，查询由 cypher_engine 的参数化 Cypher 子集执行。
"""
from collections import Counter
from pathlib import Path

import config
import schema
from utils import read_json, write_json


class GraphStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.GRAPH_PATH)
        self.nodes: dict[str, dict] = {}   # key = "Label::name"
        self.edges: dict[str, dict] = {}   # key = "sLabel::sName -[REL]-> oLabel::oName"
        self.load()

    # ---------- 持久化 ----------
    def load(self) -> None:
        data = read_json(self.path, {})
        for node in data.get("nodes", []):
            self.nodes[self._node_key(node["label"], node["name"])] = node
        for edge in data.get("edges", []):
            self.edges[self._edge_key(edge)] = edge

    def save(self) -> None:
        write_json(self.path, {
            "nodes": sorted(self.nodes.values(), key=lambda n: (n["label"], n["name"])),
            "edges": sorted(self.edges.values(), key=lambda e: (e["from_label"], e["from"], e["type"], e["to"])),
        })

    # ---------- key ----------
    @staticmethod
    def _node_key(label: str, name: str) -> str:
        return f"{label}::{name}"

    @staticmethod
    def _edge_key(edge: dict) -> str:
        return f"{edge['from_label']}::{edge['from']} -[{edge['type']}]-> {edge['to_label']}::{edge['to']}"

    # ---------- 写 ----------
    def merge_node(self, label: str, name: str, props: dict | None = None) -> dict:
        key = self._node_key(label, name)
        node = self.nodes.get(key)
        if node is None:
            node = {"label": label, "name": name, "props": {}}
            self.nodes[key] = node
        for k, v in (props or {}).items():
            if v not in (None, "", [], {}):
                node["props"][k] = v
        return node

    def merge_relation(self, from_label: str, from_name: str, rel: str,
                       to_label: str, to_name: str, props: dict | None = None) -> bool:
        """返回 True 表示新建关系，False 表示已存在（幂等命中）。"""
        edge = {
            "from_label": from_label, "from": from_name, "type": rel,
            "to_label": to_label, "to": to_name,
            "props": {k: v for k, v in (props or {}).items() if v not in (None, "")},
        }
        key = self._edge_key(edge)
        if key in self.edges:
            self.edges[key]["props"].update(edge["props"])
            return False
        self.edges[key] = edge
        return True

    # ---------- 读 ----------
    def node(self, label: str, name: str) -> dict | None:
        return self.nodes.get(self._node_key(label, name))

    def resolve(self, name: str) -> tuple[str, str] | None:
        """输入名称 -> (label, 规范名)。优先真实实体，其次别名关系，最后才回落到别名占位节点。"""
        if not name:
            return None
        for label in schema.LABELS:
            node = self.node(label, name)
            if node and not node["props"].get("alias_only"):
                return label, name
        for edge in self.edges.values():
            if edge["type"] == schema.ALIAS_OF and edge["to"] == name:
                return edge["from_label"], edge["from"]
        for label in schema.LABELS:
            if self.node(label, name):
                return label, name
        return None

    def neighbors(self, from_label: str, from_name: str, rel: str) -> list[dict]:
        out = []
        for edge in self.edges.values():
            if edge["from_label"] == from_label and edge["from"] == from_name and edge["type"] == rel:
                node = self.node(edge["to_label"], edge["to"]) or {"label": edge["to_label"], "name": edge["to"], "props": {}}
                out.append(node)
        return sorted(out, key=lambda n: n["name"])

    def entities(self) -> list[dict]:
        return [{
            "name": node["name"],
            "label": node["label"],
            "aliases": node.get("props", {}).get("alias", []) or [],
            "desc": node.get("props", {}).get("desc", ""),
        } for node in self.nodes.values()]

    def stats(self) -> dict:
        node_by_label = Counter(n["label"] for n in self.nodes.values())
        edge_by_type = Counter(e["type"] for e in self.edges.values())
        alias_only = sum(1 for n in self.nodes.values() if n.get("props", {}).get("alias_only"))
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "nodes_by_label": {label: node_by_label.get(label, 0) for label in schema.LABELS},
            "edges_by_type": {rel: edge_by_type.get(rel, 0) for rel in schema.RELATIONS},
            "alias_only_nodes": alias_only,
        }

    def clear(self) -> None:
        self.nodes.clear()
        self.edges.clear()
        self.save()


_CACHE: dict = {"mtime": -1.0, "graph": None}


def load_graph() -> GraphStore:
    """带 mtime 缓存：图谱文件未变时复用同一实例，避免高频问答反复解析 JSON。"""
    try:
        mtime = config.GRAPH_PATH.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _CACHE["graph"] is None or _CACHE["mtime"] != mtime:
        _CACHE["graph"] = GraphStore()
        _CACHE["mtime"] = mtime
    return _CACHE["graph"]
