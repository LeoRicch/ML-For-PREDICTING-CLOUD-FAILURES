"""Versioned locally trained model loading. Never load user-supplied pickle files."""
import json
from pathlib import Path
import joblib
import numpy as np
from .simulator import FEATURES


class Model:
    def __init__(self, directory):
        directory = Path(directory)
        self.metadata = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        self.estimator = joblib.load(directory / "model.joblib")
        self.version = self.metadata["version"]
        self.threshold = self.metadata["threshold"]

    def predict(self, rows):
        if not rows:
            return []
        x = np.array([[r[f] for f in FEATURES] for r in rows], dtype=np.float32)
        return self.estimator.predict_proba(x)[:, list(self.estimator.classes_).index(1)].tolist()


def latest_model(root):
    root = Path(root)
    pointer = root / "models" / "current.json"
    if not pointer.exists():
        return None
    version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
    if Path(version).name != version:
        raise ValueError("Invalid local model version.")
    return Model(root / "models" / version)
