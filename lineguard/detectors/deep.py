"""Deep sequence baselines (PyTorch, CPU-friendly).

Re-implementations of the two strongest deep models on the SKAB leaderboard,
using SKAB's decision rule so the comparison is like-for-like:
60-step windows, limit = 4/3 x 99.9th percentile of training reconstruction
error, and a point is flagged only when the preceding 59 windows all exceed it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

try:
    import torch
    from torch import nn
except ImportError:  # deep models are optional
    torch = None
    nn = None

STEPS = 60


def _windows(values: np.ndarray, steps: int = STEPS) -> np.ndarray:
    return np.stack([values[i:i + steps] for i in range(len(values) - steps + 1)])


if nn is not None:
    class ConvAE(nn.Module):
        def __init__(self, n_features: int):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Conv1d(n_features, 32, 7, padding=3, stride=2), nn.ReLU(), nn.Dropout(0.2),
                nn.Conv1d(32, 16, 7, padding=3, stride=2), nn.ReLU(),
            )
            self.decoder = nn.Sequential(
                nn.ConvTranspose1d(16, 16, 7, padding=3, stride=2, output_padding=1), nn.ReLU(), nn.Dropout(0.2),
                nn.ConvTranspose1d(16, 32, 7, padding=3, stride=2, output_padding=1), nn.ReLU(),
                nn.Conv1d(32, n_features, 7, padding=3),
            )

        def forward(self, x):  # x: batch, steps, features
            return self.decoder(self.encoder(x.transpose(1, 2))).transpose(1, 2)

    class LSTMAE(nn.Module):
        def __init__(self, n_features: int, hidden: int = 32):
            super().__init__()
            self.encoder = nn.LSTM(n_features, hidden, batch_first=True)
            self.decoder = nn.LSTM(hidden, hidden, batch_first=True)
            self.output = nn.Linear(hidden, n_features)

        def forward(self, x):
            _, (h, _) = self.encoder(x)
            repeated = h[-1].unsqueeze(1).repeat(1, x.shape[1], 1)
            decoded, _ = self.decoder(repeated)
            return self.output(decoded)


def _fit(model, windows: np.ndarray, epochs: int, seed: int):
    torch.manual_seed(seed)
    data = torch.tensor(windows, dtype=torch.float32)
    split = int(len(data) * 0.9)
    train, val = data[:split], data[split:]
    optimiser = torch.optim.Adam(model.parameters(), lr=1e-3)
    best, best_state, patience = np.inf, None, 0
    for _ in range(epochs):
        model.train()
        for batch in torch.split(train[torch.randperm(len(train))], 32):
            optimiser.zero_grad()
            loss = ((model(batch) - batch) ** 2).mean()
            loss.backward()
            optimiser.step()
        model.eval()
        with torch.no_grad():
            v = ((model(val) - val) ** 2).mean().item()
        if v < best - 1e-5:
            best, best_state, patience = v, {k: t.clone() for k, t in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= 5:
                break
    if best_state:
        model.load_state_dict(best_state)
    return model


def _errors(model, windows: np.ndarray) -> np.ndarray:
    with torch.no_grad():
        x = torch.tensor(windows, dtype=torch.float32)
        return (model(x) - x).abs().mean(dim=1).sum(dim=1).numpy()


def autoencoder_scores(kind: str, train: pd.DataFrame, test: pd.DataFrame,
                       epochs: int = 60, seed: int = 0) -> np.ndarray:
    """Reconstruction error / limit for each window, aligned to the window's
    first row (SKAB convention); padded with NaN at the tail."""
    if torch is None:
        raise ImportError("Install torch to run the deep baselines")
    torch.set_num_threads(1)
    scaler = StandardScaler().fit(train)
    w_train = _windows(scaler.transform(train))
    w_test = _windows(scaler.transform(test))
    model = (ConvAE if kind == "conv" else LSTMAE)(train.shape[1])
    model = _fit(model, w_train, epochs, seed)
    limit = np.quantile(_errors(model, w_train), 0.999) * 4 / 3
    ratio = np.full(len(test), np.nan)
    ratio[: len(w_test)] = _errors(model, w_test) / limit
    return ratio


def skab_rule(ratio: np.ndarray) -> np.ndarray:
    """SKAB decision rule: flag i when windows i-STEPS+1 .. i-1 all exceed the limit."""
    n_windows = int(np.sum(~np.isnan(ratio)))
    exceed = np.nan_to_num(ratio[:n_windows]) > 1.0
    flagged = np.zeros(len(ratio), dtype=bool)
    # SKAB rule: flag index i when windows (i - STEPS + 1 .. i - 1) all exceed.
    for i in range(STEPS - 1, len(exceed) - STEPS + 1):
        if exceed[i - STEPS + 1:i].all():
            flagged[i] = True
    return flagged


def autoencoder_detect(kind: str, train: pd.DataFrame, test: pd.DataFrame,
                       epochs: int = 60, seed: int = 0) -> np.ndarray:
    return skab_rule(autoencoder_scores(kind, train, test, epochs, seed))
