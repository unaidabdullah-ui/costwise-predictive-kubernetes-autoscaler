"""A small LSTM forecaster implemented with NumPy only.

Why NumPy? The demo runs on a 4 GB laptop. PyTorch would add a 700 MB+ install
and 300 MB+ of RAM for a network with ~6 000 parameters. The architecture and
maths below are the standard LSTM (same gate layout as ``torch.nn.LSTM``), so
weights from a single-layer PyTorch model can be loaded unchanged
(:func:`ml.src.model.lstm_weights_numpy`).

Network::

    x_1..x_T (T × F)  ->  LSTM(H)  ->  h_T  ->  Linear(H, H)  ->  ReLU  ->  Linear(H, O)

LSTM cell (gate order i, f, g, o)::

    z_t = [x_t, h_{t-1}] W + b
    i = σ(z_i)   f = σ(z_f)   g = tanh(z_g)   o = σ(z_o)
    c_t = f ⊙ c_{t-1} + i ⊙ g
    h_t = o ⊙ tanh(c_t)

Training uses back-propagation through time (BPTT) with the Adam optimiser and
global-norm gradient clipping. :func:`gradient_check` compares the analytic
gradients with finite differences (used by the test-suite).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

PARAM_NAMES: tuple[str, ...] = ("W", "b", "W1", "b1", "W2", "b2")


def _sigmoid(x: np.ndarray) -> np.ndarray:
    # numerically stable logistic
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    ex = np.exp(x[~pos])
    out[~pos] = ex / (1.0 + ex)
    return out


@dataclass
class _Cache:
    xh: list[np.ndarray]
    gates: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
    c: list[np.ndarray]
    h_last: np.ndarray
    a1: np.ndarray
    r: np.ndarray


class NumpyLSTM:
    """Single-layer LSTM + two-layer MLP head for direct multi-step regression."""

    def __init__(self, n_features: int, hidden_size: int, n_outputs: int, seed: int = 0) -> None:
        if min(n_features, hidden_size, n_outputs) < 1:
            raise ValueError("n_features, hidden_size and n_outputs must be >= 1")
        self.n_features = n_features
        self.hidden_size = hidden_size
        self.n_outputs = n_outputs
        rng = np.random.default_rng(seed)
        fan_in = n_features + hidden_size
        limit = np.sqrt(6.0 / (fan_in + hidden_size))
        b = np.zeros(4 * hidden_size)
        b[hidden_size : 2 * hidden_size] = 1.0  # forget-gate bias 1: remember by default
        self.params: dict[str, np.ndarray] = {
            "W": rng.uniform(-limit, limit, size=(fan_in, 4 * hidden_size)),
            "b": b,
            "W1": rng.normal(0.0, np.sqrt(2.0 / hidden_size), size=(hidden_size, hidden_size)),
            "b1": np.zeros(hidden_size),
            "W2": rng.normal(0.0, np.sqrt(1.0 / hidden_size), size=(hidden_size, n_outputs)) * 0.1,
            "b2": np.zeros(n_outputs),
        }

    # ------------------------------------------------------------------ forward
    def _forward(self, x: np.ndarray) -> tuple[np.ndarray, _Cache]:
        if x.ndim != 3 or x.shape[2] != self.n_features:
            raise ValueError(f"expected input (batch, T, {self.n_features}), got {x.shape}")
        p, hs = self.params, self.hidden_size
        batch, steps, _ = x.shape
        h = np.zeros((batch, hs))
        c = np.zeros((batch, hs))
        cache = _Cache(xh=[], gates=[], c=[c], h_last=h, a1=h, r=h)
        for t in range(steps):
            xh = np.concatenate([x[:, t, :], h], axis=1)
            z = xh @ p["W"] + p["b"]
            i = _sigmoid(z[:, :hs])
            f = _sigmoid(z[:, hs : 2 * hs])
            g = np.tanh(z[:, 2 * hs : 3 * hs])
            o = _sigmoid(z[:, 3 * hs :])
            c = f * c + i * g
            h = o * np.tanh(c)
            cache.xh.append(xh)
            cache.gates.append((i, f, g, o))
            cache.c.append(c)
        a1 = h @ p["W1"] + p["b1"]
        r = np.maximum(a1, 0.0)
        y = r @ p["W2"] + p["b2"]
        cache.h_last, cache.a1, cache.r = h, a1, r
        return y, cache

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Outputs ``(batch, n_outputs)`` for inputs ``(batch, T, n_features)``."""
        y, _ = self._forward(np.asarray(x, dtype=float))
        return y

    # ----------------------------------------------------------------- backward
    def loss_and_grads(self, x: np.ndarray, target: np.ndarray) -> tuple[float, dict[str, np.ndarray]]:
        """Mean squared error and its gradients with respect to every parameter."""
        x = np.asarray(x, dtype=float)
        target = np.asarray(target, dtype=float)
        y, cache = self._forward(x)
        if target.shape != y.shape:
            raise ValueError(f"target shape {target.shape} != output shape {y.shape}")
        diff = y - target
        loss = float(np.mean(diff**2))
        p = self.params
        grads = {k: np.zeros_like(v) for k, v in p.items()}
        dy = 2.0 * diff / diff.size
        grads["W2"] = cache.r.T @ dy
        grads["b2"] = dy.sum(axis=0)
        dr = dy @ p["W2"].T
        da1 = dr * (cache.a1 > 0)
        grads["W1"] = cache.h_last.T @ da1
        grads["b1"] = da1.sum(axis=0)
        dh = da1 @ p["W1"].T
        dc = np.zeros_like(dh)
        for t in reversed(range(len(cache.xh))):
            i, f, g, o = cache.gates[t]
            c_t, c_prev = cache.c[t + 1], cache.c[t]
            tanh_c = np.tanh(c_t)
            do = dh * tanh_c
            dc = dc + dh * o * (1.0 - tanh_c**2)
            di = dc * g
            dg = dc * i
            df = dc * c_prev
            dz = np.concatenate(
                [di * i * (1.0 - i), df * f * (1.0 - f), dg * (1.0 - g**2), do * o * (1.0 - o)], axis=1
            )
            grads["W"] += cache.xh[t].T @ dz
            grads["b"] += dz.sum(axis=0)
            dxh = dz @ p["W"].T
            dh = dxh[:, self.n_features :]
            dc = dc * f
        return loss, grads

    # ---------------------------------------------------------------- utilities
    def parameter_count(self) -> int:
        return int(sum(v.size for v in self.params.values()))

    def copy_params(self) -> dict[str, np.ndarray]:
        return {k: v.copy() for k, v in self.params.items()}

    def set_params(self, params: dict[str, np.ndarray]) -> None:
        for name in PARAM_NAMES:
            if params[name].shape != self.params[name].shape:
                raise ValueError(f"parameter {name} has shape {params[name].shape}, "
                                 f"expected {self.params[name].shape}")
        self.params = {k: np.asarray(params[k], dtype=float).copy() for k in PARAM_NAMES}

    def save(self, path: Path, metadata: dict[str, Any] | None = None) -> None:
        """Write weights + architecture + JSON metadata into one ``.npz`` file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        arch = np.array([self.n_features, self.hidden_size, self.n_outputs])
        meta = np.array(json.dumps(metadata or {}))
        with path.open("wb") as fh:  # explicit handle: np.savez would append ".npz" to tmp names
            np.savez(fh, arch=arch, metadata=meta, **self.params)

    @classmethod
    def load(cls, path: Path) -> tuple[NumpyLSTM, dict[str, Any]]:
        """Load a model written by :meth:`save`; returns ``(model, metadata)``."""
        with np.load(path, allow_pickle=False) as data:
            n_features, hidden, n_out = (int(v) for v in data["arch"])
            model = cls(n_features, hidden, n_out)
            model.set_params({k: data[k] for k in PARAM_NAMES})
            metadata = json.loads(str(data["metadata"]))
        return model, metadata


class Adam:
    """Adam optimiser (Kingma & Ba) with optional global-norm gradient clipping."""

    def __init__(self, params: dict[str, np.ndarray], lr: float = 3e-3, beta1: float = 0.9,
                 beta2: float = 0.999, eps: float = 1e-8, clip_norm: float | None = 1.0) -> None:
        self.lr, self.beta1, self.beta2, self.eps, self.clip_norm = lr, beta1, beta2, eps, clip_norm
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params: dict[str, np.ndarray], grads: dict[str, np.ndarray]) -> float:
        """Update ``params`` in place; returns the (pre-clipping) gradient norm."""
        norm = float(np.sqrt(sum(float(np.sum(g * g)) for g in grads.values())))
        scale = 1.0
        if self.clip_norm is not None and norm > self.clip_norm:
            scale = self.clip_norm / (norm + 1e-12)
        self.t += 1
        for k in params:
            g = grads[k] * scale
            self.m[k] = self.beta1 * self.m[k] + (1 - self.beta1) * g
            self.v[k] = self.beta2 * self.v[k] + (1 - self.beta2) * g * g
            m_hat = self.m[k] / (1 - self.beta1**self.t)
            v_hat = self.v[k] / (1 - self.beta2**self.t)
            params[k] -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
        return norm


def gradient_check(model: NumpyLSTM, x: np.ndarray, target: np.ndarray, eps: float = 1e-5,
                   samples_per_param: int = 6, seed: int = 0) -> float:
    """Largest relative error between analytic and central-difference gradients."""
    rng = np.random.default_rng(seed)
    _, grads = model.loss_and_grads(x, target)
    worst = 0.0
    for name, param in model.params.items():
        for _ in range(samples_per_param):
            idx = tuple(rng.integers(0, s) for s in param.shape)
            original = param[idx]
            param[idx] = original + eps
            plus, _ = model.loss_and_grads(x, target)
            param[idx] = original - eps
            minus, _ = model.loss_and_grads(x, target)
            param[idx] = original
            numeric = (plus - minus) / (2 * eps)
            analytic = grads[name][idx]
            denom = max(abs(numeric) + abs(analytic), 1e-8)
            worst = max(worst, abs(numeric - analytic) / denom)
    return worst
