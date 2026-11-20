"""数据资产：实体表 / 关系候选表 / 训练集三类派生资产（词典由 segment.build_dict 产出）。"""
import schema
import config
from utils import write_json


def build_assets(records: list[dict], triples: list[dict]) -> dict:
    entity_table: dict[tuple[str, str], dict] = {}
    relation_candidates: list[dict] = []
    protected_samples: list[dict] = []

    for record in records:
        name = record.get("name")
        if name:
            entity_table[(schema.DISEASE, name)] = {
                "name": name, "label": schema.DISEASE,
                "type": record.get("type", ""), "desc": record.get("desc", ""),
                "source": record.get("source", ""), "url": record.get("url", ""),
            }
        for field, label in schema.FIELD_TO_LABEL.items():
            for item in record.get(field) or []:
                entity_table.setdefault((label, item), {
                    "name": item, "label": label, "source": record.get("source", ""),
                })
        from clean import protected_terms
        hits = protected_terms(record.get("raw_text", ""))
        if hits:
            protected_samples.append({"name": name, "terms": hits[:10]})

    for triple in triples:
        relation_candidates.append({
            "subject": triple["subject"], "subject_label": triple["subject_label"],
            "relation": triple["relation"], "object": triple["object"],
            "object_label": triple["object_label"], "source": triple.get("source", ""),
            "url": triple.get("url", ""), "date": triple.get("date", ""),
        })

    entities = list(entity_table.values())
    write_json(config.CORPUS_DIR / "entity_table.json", entities)
    write_json(config.CORPUS_DIR / "relation_candidates.json", relation_candidates)
    write_json(config.CORPUS_DIR / "protected_samples.json", protected_samples)
    return {"entities": entities, "relations": relation_candidates}
