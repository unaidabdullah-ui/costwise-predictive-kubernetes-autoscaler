"""PyTorch LSTM for direct multi-step forecasting (optional, offline pipeline).

    input (batch, L, F) → LSTM(hidden, layers) → last hidden state
      → Linear(hidden, hidden) → ReLU → Dropout → Linear(hidden, H) → (batch, H)

The demo uses the NumPy implementation of the same idea (:mod:`ml.src.numpy_lstm`);
``export_to_lite`` converts a single-layer PyTorch model into that format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn


class LSTMForecaster(nn.Module):
    """Stacked LSTM followed by a small MLP head."""

    def __init__(self, n_features: int, hidden_size: int = 32, num_layers: int = 2,
                 dropout: float = 0.2, horizon: int = 4) -> None:
        super().__init__()
        if n_features < 1 or hidden_size < 1 or num_layers < 1 or horizon < 1:
            raise ValueError("n_features, hidden_size, num_layers and horizon must be >= 1")
        self.n_features, self.hidden_size, self.num_layers = n_features, hidden_size, num_layers
        self.dropout, self.horizon = dropout, horizon
        self.lstm = nn.LSTM(n_features, hidden_size, num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Linear(hidden_size, hidden_size), nn.ReLU(),
                                  nn.Dropout(dropout), nn.Linear(hidden_size, horizon))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() != 3:
            raise ValueError(f"expected (batch, seq_len, features), got {tuple(x.shape)}")
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :])

    def hyperparameters(self) -> dict[str, Any]:
        return {"n_features": self.n_features, "hidden_size": self.hidden_size,
                "num_layers": self.num_layers, "dropout": self.dropout, "horizon": self.horizon}


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def save_model(model: LSTMForecaster, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "hyperparameters": model.hyperparameters()}, path)


def load_model(path: Path, device: str | torch.device = "cpu") -> LSTMForecaster:
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    model = LSTMForecaster(**checkpoint["hyperparameters"])
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device)
    model.eval()
    return model


@torch.no_grad()
def predict_array(model: nn.Module, x: np.ndarray, device: str | torch.device = "cpu",
                  batch_size: int = 1024) -> np.ndarray:
    """Batched inference on ``(N, L, F)``; returns ``(N, H)``."""
    model.eval()
    outputs = []
    for start in range(0, len(x), batch_size):
        batch = torch.as_tensor(x[start : start + batch_size], dtype=torch.float32, device=device)
        outputs.append(model(batch).cpu().numpy())
    if not outputs:
        return np.empty((0, getattr(model, "horizon", 1)), dtype=np.float32)
    return np.concatenate(outputs)


def resolve_device(name: str = "auto") -> torch.device:
    """``auto`` uses CUDA (e.g. an RTX GPU) when available, otherwise the CPU."""
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


def lstm_weights_numpy(model: LSTMForecaster) -> dict[str, np.ndarray]:
    """Weights of a 1-layer model in the NumPy LSTM layout (gate order i, f, g, o)."""
    if model.num_layers != 1:
        raise ValueError("only single-layer models can be exported to the NumPy LSTM")
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    return {
        "W": np.concatenate([sd["lstm.weight_ih_l0"], sd["lstm.weight_hh_l0"]], axis=1).T,
        "b": sd["lstm.bias_ih_l0"] + sd["lstm.bias_hh_l0"],
        "W1": sd["head.0.weight"].T, "b1": sd["head.0.bias"],
        "W2": sd["head.3.weight"].T, "b2": sd["head.3.bias"],
    }
