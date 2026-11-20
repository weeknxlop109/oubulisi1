"""NER（BiLSTM-CRF）与意图分类（TextCNN）的模型定义与推理。

训练用 train.py；在线推理只走本文件的 load() / predict_*()，模型缺失时回退词典与规则。
"""
import torch
import torch.nn as nn

import config
import schema

PAD, UNK = 0, 1
PAD_TAG = "<pad>"
NER_LABELS = [PAD_TAG, "O"]
for label in schema.LABELS:
    NER_LABELS += [f"B-{label}", f"I-{label}"]
NER_LABEL2IDX = {name: i for i, name in enumerate(NER_LABELS)}


class CRF(nn.Module):
    def __init__(self, num_tags: int):
        super().__init__()
        self.num_tags = num_tags
        self.start = nn.Parameter(torch.randn(num_tags) * 0.1)
        self.end = nn.Parameter(torch.randn(num_tags) * 0.1)
        self.transitions = nn.Parameter(torch.randn(num_tags, num_tags) * 0.1)

    def _masked(self, emissions, mask):
        emit = emissions.clone()
        emit[:, :, PAD] = -1e4
        emit = emit.masked_fill(mask.unsqueeze(-1) == 0, -1e4)
        return emit

    def log_likelihood(self, emissions, tags, mask):
        emit = self._masked(emissions, mask)
        lengths = mask.sum(dim=1).long()
        batch = emit.size(0)
        seq = emit.size(1)
        score = self.start[tags[:, 0]] + emit[torch.arange(batch), 0, tags[:, 0]]
        for i in range(1, seq):
            step_mask = mask[:, i]
            score = score + (self.transitions[tags[:, i - 1], tags[:, i]]
                             + emit[torch.arange(batch), i, tags[:, i]]) * step_mask
        score = score + self.end[tags[torch.arange(batch), (lengths - 1).clamp(min=0)]]

        alpha = self.start.unsqueeze(0) + emit[:, 0]
        for i in range(1, seq):
            nxt = torch.logsumexp(alpha.unsqueeze(2) + self.transitions.unsqueeze(0)
                                  + emit[:, i].unsqueeze(1), dim=1)
            alpha = torch.where(mask[:, i].unsqueeze(1).bool(), nxt, alpha)
        alpha = alpha + self.end
        log_z = torch.logsumexp(alpha, dim=1)
        return score - log_z

    def decode(self, emissions, mask):
        emit = self._masked(emissions, mask)
        batch, seq = emit.size(0), emit.size(1)
        score = self.start.unsqueeze(0) + emit[:, 0]
        history = []
        for i in range(1, seq):
            nxt = score.unsqueeze(2) + self.transitions.unsqueeze(0) + emit[:, i].unsqueeze(1)
            best, idx = nxt.max(dim=1)
            keep = mask[:, i].unsqueeze(1).bool()
            score = torch.where(keep, best, score)
            history.append(torch.where(keep, idx, torch.zeros_like(idx)))
        score = score + self.end
        best_last = score.argmax(dim=1)
        paths = [best_last]
        for idx in reversed(history):
            best_last = idx.gather(1, best_last.unsqueeze(1)).squeeze(1)
            paths.append(best_last)
        return torch.stack(list(reversed(paths)), dim=1)


class BiLSTMCRF(nn.Module):
    def __init__(self, vocab_size: int, num_tags: int, emb: int = 64, hidden: int = 64):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb, hidden // 2, num_layers=1, bidirectional=True, batch_first=True)
        self.dropout = nn.Dropout(0.2)
        self.linear = nn.Linear(hidden, num_tags)
        self.crf = CRF(num_tags)

    def emissions(self, x, mask):
        return self.linear(self.dropout(self.lstm(self.embedding(x))[0]))

    def loss(self, x, tags, mask):
        emit = self.emissions(x, mask)
        return -self.crf.log_likelihood(emit, tags, mask).mean()

    def predict(self, x, mask):
        return self.crf.decode(self.emissions(x, mask), mask)


class TextCNN(nn.Module):
    def __init__(self, vocab_size: int, num_class: int, emb: int = 64, filters: int = 64):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, emb, padding_idx=PAD)
        self.convs = nn.ModuleList([nn.Conv1d(emb, filters, k, padding=k - 1) for k in (2, 3, 4)])
        self.dropout = nn.Dropout(0.3)
        self.fc = nn.Linear(filters * 3, num_class)

    def forward(self, x):
        emb = self.embedding(x).transpose(1, 2)
        pooled = [torch.relu(conv(emb)).max(dim=2).values for conv in self.convs]
        return self.fc(self.dropout(torch.cat(pooled, dim=1)))


def pad_sequences(seqs: list[list[int]], pad_value: int = PAD):
    max_len = max((len(s) for s in seqs), default=1)
    ids = torch.zeros((len(seqs), max_len), dtype=torch.long)
    mask = torch.zeros((len(seqs), max_len), dtype=torch.long)
    for i, seq in enumerate(seqs):
        ids[i, :len(seq)] = torch.tensor(seq, dtype=torch.long)
        mask[i, :len(seq)] = 1
        if not seq:
            mask[i, 0] = 1
    return ids, mask


class Predictor:
    def __init__(self, ckpt: dict):
        self.ckpt = ckpt
        self.char2idx = ckpt["char2idx"]
        self.intent_names = ckpt["intents"]
        self.ner_model = BiLSTMCRF(len(self.char2idx), len(NER_LABELS),
                                   ckpt["config"]["emb"], ckpt["config"]["hidden"])
        self.intent_model = TextCNN(len(self.char2idx), len(self.intent_names),
                                    ckpt["config"]["emb"], ckpt["config"]["filters"])
        self.ner_model.load_state_dict(ckpt["ner_state"])
        self.intent_model.load_state_dict(ckpt["intent_state"])
        self.ner_model.eval()
        self.intent_model.eval()

    def _encode(self, text: str) -> list[int]:
        return [self.char2idx.get(ch, UNK) for ch in text if not ch.isspace()]

    def ner(self, text: str) -> dict:
        ids = self._encode(text)
        if not ids:
            return {"tokens": [], "tags": [], "spans": [], "confidence": 0.0}
        x, mask = pad_sequences([ids])
        with torch.no_grad():
            logits = self.ner_model.emissions(x, mask)
            probs = torch.softmax(logits, dim=-1)
            tags = self.ner_model.predict(x, mask)[0].tolist()
        tokens = [ch for ch in text if not ch.isspace()]
        spans, cur = [], None
        confs = []
        for i, (tok, tag_idx) in enumerate(zip(tokens, tags)):
            tag = NER_LABELS[tag_idx]
            confs.append(float(probs[0, i].max()))
            if tag.startswith("B-"):
                if cur:
                    spans.append(cur)
                cur = {"text": tok, "label": tag[2:], "start": i, "end": i + 1}
            elif tag.startswith("I-") and cur and tag[2:] == cur["label"]:
                cur["text"] += tok
                cur["end"] = i + 1
            else:
                if cur:
                    spans.append(cur)
                cur = None
        if cur:
            spans.append(cur)
        return {"tokens": tokens, "tags": [NER_LABELS[t] for t in tags], "spans": spans,
                "confidence": round(sum(confs) / len(confs), 4) if confs else 0.0}

    def intent(self, text: str) -> dict:
        ids = self._encode(text)
        if not ids:
            return {"intent": "", "confidence": 0.0, "probs": {}}
        x, _ = pad_sequences([ids])
        with torch.no_grad():
            probs = torch.softmax(self.intent_model(x), dim=-1)[0]
        idx = int(probs.argmax())
        return {
            "intent": self.intent_names[idx],
            "confidence": round(float(probs[idx]), 4),
            "probs": {self.intent_names[i]: round(float(probs[i]), 4) for i in range(len(self.intent_names))},
        }


_CACHE: Predictor | None = None


def load(force: bool = False) -> Predictor | None:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    if not config.MODEL_PATH.exists():
        return None
    try:
        ckpt = torch.load(config.MODEL_PATH, map_location="cpu", weights_only=False)
        _CACHE = Predictor(ckpt)
    except (RuntimeError, KeyError, OSError, ValueError):
        _CACHE = None
    return _CACHE


def vocab_of(texts: list[str]) -> dict[str, int]:
    chars = sorted({ch for text in texts for ch in text if not ch.isspace()})
    vocab = {"<pad>": PAD, "<unk>": UNK}
    for i, ch in enumerate(chars, start=2):
        vocab[ch] = i
    return vocab
