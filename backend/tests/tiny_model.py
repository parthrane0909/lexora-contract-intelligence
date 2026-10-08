"""Shared helper: build a tiny randomly-initialized BERT classifier bundle."""
import json
import os

TINY_LABELS = ["payment", "termination", "other"]


def build_tiny_model(d: str, labels: list[str]) -> str:
    from transformers import BertConfig, BertForSequenceClassification, BertTokenizer

    os.makedirs(d, exist_ok=True)
    tok = BertTokenizer.from_pretrained("bert-base-uncased")  # ~230KB vocab, cached
    tok.save_pretrained(d)
    cfg = BertConfig(vocab_size=tok.vocab_size, hidden_size=32, num_hidden_layers=2,
                     num_attention_heads=2, intermediate_size=64, num_labels=len(labels))
    BertForSequenceClassification(cfg).save_pretrained(d, safe_serialization=False)
    json.dump({"labels": labels}, open(os.path.join(d, "label_map.json"), "w"))
    json.dump({"base_model": "tiny-test", "model_version": "tiny-v0", "labels": labels,
               "seq_len": 64}, open(os.path.join(d, "model_info.json"), "w"))
    return d
