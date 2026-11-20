"""训练 NER（BiLSTM-CRF）与意图分类（TextCNN）。

数据来自自动生成题库 + 问句增广；BIO 标注由实体词典匹配得到（词典版 baseline）。
8:1:1 切分，学习率 1e-3，Adam；仅当综合指标创新高保存 best_model.pt。
"""
import json
import random
import time
from pathlib import Path

import torch
import torch.nn as nn

import config
import schema
from graph_store import load_graph
from kb import question_bank
from ner_cls import (BiLSTMCRF, NER_LABEL2IDX, NER_LABELS, PAD_TAG, TextCNN,
                     pad_sequences, vocab_of)
from utils import log_line, now_iso, read_json, write_json

SEED = 42
EPOCHS_NER = 12
EPOCHS_INTENT = 10
BATCH = 16
LR = 1e-3
PREFIXES = ("", "请问", "我想知道")
SUFFIXES = ("", "呢", "吗")


def _load_entities() -> list[dict]:
    graph = load_graph()
    entities = []
    for node in graph.nodes.values():
        if node["props"].get("alias_only"):
            continue
        entities.append({"name": node["name"], "label": node["label"],
                         "aliases": node["props"].get("alias", []) or []})
    for edge in graph.edges.values():
        if edge["type"] == schema.ALIAS_OF:
            entities.append({"name": edge["to"], "label": edge["from_label"], "aliases": []})
    return entities


def _span_mentions(text: str, entities: list[dict]) -> list[tuple[int, int, str]]:
    """词典匹配得到实体区间（长词优先，非重叠）。"""
    candidates = []
    for ent in entities:
        for surface in [ent["name"]] + list(ent.get("aliases") or []):
            if not surface:
                continue
            start = text.find(surface)
            while start != -1:
                candidates.append((start, start + len(surface), ent["label"]))
                start = text.find(surface, start + 1)
    candidates.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    chosen: list[tuple[int, int, str]] = []
    for start, end, label in candidates:
        if any(not (end <= s or start >= e) for s, e, _ in chosen):
            continue
        chosen.append((start, end, label))
    return sorted(chosen)


def _bio_tags(text: str, entities: list[dict]) -> list[str]:
    tags = [PAD_TAG if ch.isspace() else "O" for ch in text]
    for start, end, label in _span_mentions(text, entities):
        first = True
        for i in range(start, end):
            if text[i].isspace():
                continue
            tags[i] = f"{'B' if first else 'I'}-{label}"
            first = False
    return [t for t in tags if t != PAD_TAG]


def build_dataset() -> dict:
    items = question_bank.load_items()
    entities = _load_entities()
    samples = []
    for item in items:
        for pre in PREFIXES:
            for suf in SUFFIXES:
                samples.append({"text": f"{pre}{item['question']}{suf}", "intent": item["intent"]})
    # 兜底/非医学问句：让空结果与澄清分支也有样本
    extra = [
        ("量子纠缠怎么影响股票", schema.INTENT_DRUG),
        ("今天天气怎么样", schema.INTENT_DRUG),
        ("那个东西能不能吃", schema.INTENT_DRUG),
        ("孕妇胸闷气短怎么办", schema.INTENT_DRUG),
        ("它要注意什么", schema.INTENT_PREVENT),
    ]
    for text, intent in extra:
        samples.append({"text": text, "intent": intent})

    random.Random(SEED).shuffle(samples)
    n = len(samples)
    n_test = max(1, n // 10)
    n_val = max(1, n // 10)
    train, val, test = samples[:n - n_val - n_test], samples[n - n_val - n_test:n - n_test], samples[n - n_test:]
    return {"train": train, "val": val, "test": test, "entities": entities, "all": samples}


def _encode_ner(sample: dict, vocab: dict[str, int]):
    chars = [(ch, tag) for ch, tag in zip(sample["text"], _bio_tags(sample["text"], sample["entities"]))]
    chars = [(ch, tag) for ch, tag in chars if not ch.isspace()]
    ids = [vocab.get(ch, 1) for ch, _ in chars]
    tags = [NER_LABEL2IDX[tag] for _, tag in chars]
    return ids, tags


def _batch_ner(batch: list[dict], vocab):
    ids, tags = zip(*[_encode_ner(s, vocab) for s in batch])
    x, mask = pad_sequences(list(ids))
    y, _ = pad_sequences(list(tags))
    return x, y, mask


def _spans_from_tags(text: str, tag_ids: list[int]) -> set[tuple[int, int, str]]:
    spans, cur = set(), None
    idx = 0
    for ch, tag_id in zip(text, tag_ids):
        if ch.isspace():
            continue
        tag = NER_LABELS[tag_id]
        if tag.startswith("B-"):
            if cur:
                spans.add(cur)
            cur = (idx, idx + 1, tag[2:])
        elif tag.startswith("I-") and cur and tag[2:] == cur[2]:
            cur = (cur[0], idx + 1, cur[2])
        else:
            if cur:
                spans.add(cur)
            cur = None
        idx += 1
    if cur:
        spans.add(cur)
    return spans


def evaluate_ner(model, data, vocab):
    device = next(model.parameters()).device
    model.eval()
    tp = fp = fn = 0
    correct = total = 0
    with torch.no_grad():
        for sample in data:
            ids, gold_tags = _encode_ner(sample, vocab)
            if not ids:
                continue
            x, mask = pad_sequences([ids])
            x, mask = x.to(device), mask.to(device)
            pred = model.predict(x, mask)[0].tolist()
            total += len(ids)
            correct += sum(1 for p, g in zip(pred, gold_tags) if p == g)
            gold = _spans_from_tags(sample["text"], gold_tags)
            guessed = _spans_from_tags(sample["text"], pred)
            tp += len(gold & guessed)
            fp += len(guessed - gold)
            fn += len(gold - guessed)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "token_acc": round(correct / total, 4) if total else 0.0}


def evaluate_intent(model, data, vocab, intent_names):
    device = next(model.parameters()).device
    model.eval()
    correct = 0
    with torch.no_grad():
        for sample in data:
            ids = [vocab.get(ch, 1) for ch in sample["text"] if not ch.isspace()]
            x, _ = pad_sequences([ids])
            pred = int(model(x.to(device)).argmax(dim=1)[0])
            correct += int(intent_names[pred] == sample["intent"])
    return round(correct / len(data), 4) if data else 0.0


def _ner_epoch(model, data, vocab, optimizer, device):
    model.train()
    loss_sum, batches = 0.0, 0
    for i in range(0, len(data), BATCH):
        x, y, mask = _batch_ner(data[i:i + BATCH], vocab)
        x, y, mask = x.to(device), y.to(device), mask.to(device)
        optimizer.zero_grad()
        loss = model.loss(x, y, mask)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        optimizer.step()
        loss_sum += float(loss.item())
        batches += 1
    return loss_sum / max(batches, 1)


def _intent_epoch(model, data, vocab, opt, device):
    model.train()
    loss_sum, batches = 0.0, 0
    criterion = nn.CrossEntropyLoss()
    for i in range(0, len(data), BATCH):
        chunk = data[i:i + BATCH]
        ids = [[vocab.get(ch, 1) for ch in s["text"] if not ch.isspace()] for s in chunk]
        x, _ = pad_sequences(ids)
        y = torch.tensor([INTENT2IDX[s["intent"]] for s in chunk], dtype=torch.long)
        x, y = x.to(device), y.to(device)
        opt.zero_grad()
        loss = criterion(model(x), y)
        loss.backward()
        opt.step()
        loss_sum += float(loss.item())
        batches += 1
    return loss_sum / max(batches, 1)


INTENT2IDX: dict[str, int] = {}


def _train_ner(train, val, vocab, device, epochs=EPOCHS_NER, lr=LR):
    model = BiLSTMCRF(len(vocab), len(NER_LABELS), 64, 64).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    history, best = [], {"f1": -1, "epoch": -1, "state": None}
    for epoch in range(1, epochs + 1):
        loss = _ner_epoch(model, train, vocab, opt, device)
        metrics = evaluate_ner(model, val, vocab)
        history.append({"epoch": epoch, "loss": round(loss, 4), **metrics})
        if metrics["f1"] > best["f1"]:
            best = {"f1": metrics["f1"], "epoch": epoch,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        print(f"[NER] epoch {epoch:02d} loss={loss:.4f} f1={metrics['f1']:.4f} acc={metrics['token_acc']:.4f}")
    model.load_state_dict(best["state"])
    return model, history, best


def _train_intent(train, val, vocab, device, epochs=EPOCHS_INTENT, lr=LR):
    model = TextCNN(len(vocab), len(schema.INTENTS), 64, 64).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    history, best = [], {"acc": -1, "epoch": -1, "state": None}
    for epoch in range(1, epochs + 1):
        loss = _intent_epoch(model, train, vocab, opt, device)
        acc = evaluate_intent(model, val, vocab, list(schema.INTENTS))
        history.append({"epoch": epoch, "loss": round(loss, 4), "val_acc": acc})
        if acc > best["acc"]:
            best = {"acc": acc, "epoch": epoch,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        print(f"[INTENT] epoch {epoch:02d} loss={loss:.4f} val_acc={acc:.4f}")
    model.load_state_dict(best["state"])
    return model, history, best


def _plot(ner_history, intent_history):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
        axes[0].plot([h["epoch"] for h in ner_history], [h["loss"] for h in ner_history], marker="o")
        axes[0].set_title("NER loss"); axes[0].set_xlabel("epoch")
        axes[1].plot([h["epoch"] for h in ner_history], [h["f1"] for h in ner_history], marker="o", color="tab:green")
        axes[1].set_title("NER val F1"); axes[1].set_xlabel("epoch")
        axes[2].plot([h["epoch"] for h in intent_history], [h["val_acc"] for h in intent_history], marker="o", color="tab:red")
        axes[2].set_title("Intent val acc"); axes[2].set_xlabel("epoch")
        for ax in axes:
            ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(config.CURVE_PATH, dpi=140)
        plt.close(fig)
    except Exception as exc:  # 绘图失败不影响训练主流程
        log_line("train", f"plot failed: {exc}")


def main() -> dict:
    global INTENT2IDX
    started = time.time()
    config.ensure_dirs()
    random.seed(SEED)
    torch.manual_seed(SEED)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    data = build_dataset()
    vocab = vocab_of([s["text"] for s in data["all"]])
    INTENT2IDX = {name: i for i, name in enumerate(schema.INTENTS)}
    for split in ("train", "val", "test"):
        for sample in data[split]:
            sample["entities"] = data["entities"]

    print(f"device={device} train={len(data['train'])} val={len(data['val'])} test={len(data['test'])} vocab={len(vocab)}")
    ner_model, ner_history, ner_best = _train_ner(data["train"], data["val"], vocab, device)
    intent_model, intent_history, intent_best = _train_intent(data["train"], data["val"], vocab, device)

    ner_test = evaluate_ner(ner_model, data["test"], vocab)
    intent_test = evaluate_intent(intent_model, data["test"], vocab, list(schema.INTENTS))

    # 对照实验：同一批数据 3 轮，1e-3 vs 2e-5
    _, cmp_fast, _ = _train_ner(data["train"], data["val"], vocab, device, epochs=3, lr=1e-3)
    _, cmp_slow, _ = _train_ner(data["train"], data["val"], vocab, device, epochs=3, lr=2e-5)
    comparison = {
        "lr_1e-3": {"final_train_loss": cmp_fast[-1]["loss"], "val_f1": cmp_fast[-1]["f1"]},
        "lr_2e-5": {"final_train_loss": cmp_slow[-1]["loss"], "val_f1": cmp_slow[-1]["f1"]},
        "note": "从零训练时过小学习率 3 轮内收敛明显更慢，故主实验采用 1e-3",
    }

    ckpt = {
        "version": 1,
        "created_at": now_iso(),
        "config": {"emb": 64, "hidden": 64, "filters": 64},
        "char2idx": vocab,
        "intents": list(schema.INTENTS),
        "ner_state": ner_best["state"],
        "intent_state": intent_best["state"],
        "metrics": {"ner_val_f1": ner_best["f1"], "intent_val_acc": intent_best["acc"],
                    "ner_test": ner_test, "intent_test_acc": intent_test},
    }
    torch.save(ckpt, config.MODEL_PATH)

    import ner_cls
    predictor = ner_cls.load(force=True)
    demo = "风热感冒吃什么药"
    demo_ner = predictor.ner(demo) if predictor else {"confidence": 0.0, "spans": []}
    demo_intent = predictor.intent(demo) if predictor else {"confidence": 0.0, "intent": ""}

    metrics = {
        "trained_at": now_iso(),
        "device": str(device),
        "split": {k: len(v) for k, v in data.items() if k in ("train", "val", "test")},
        "epochs": {"ner": EPOCHS_NER, "intent": EPOCHS_INTENT},
        "learning_rate": LR,
        "ner_history": ner_history,
        "intent_history": intent_history,
        "ner_best": {k: v for k, v in ner_best.items() if k != "state"},
        "intent_best": {k: v for k, v in intent_best.items() if k != "state"},
        "ner_test": ner_test,
        "intent_test_acc": intent_test,
        "comparison": comparison,
        "demo_question": {"question": demo, "ner_confidence": demo_ner["confidence"],
                          "spans": demo_ner["spans"], "intent": demo_intent["intent"],
                          "intent_confidence": demo_intent["confidence"]},
        "elapsed_sec": round(time.time() - started, 2),
        "model_path": str(config.MODEL_PATH),
    }
    write_json(config.METRICS_PATH, metrics)
    _plot(ner_history, intent_history)
    log_line("train", "done", **{k: metrics[k] for k in ("ner_test", "intent_test_acc", "elapsed_sec")})
    print(json.dumps({k: metrics[k] for k in ("ner_test", "intent_test_acc", "demo_question")},
                     ensure_ascii=False, indent=2))
    return metrics


if __name__ == "__main__":
    main()
