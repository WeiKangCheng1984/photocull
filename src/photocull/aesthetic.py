from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

WEIGHT_NAME = "sac+logos+ava1-l14-linearMSE.pth"


def _build_head(torch, payload):
    if isinstance(payload, dict) and any(str(key).startswith("layers.") for key in payload):

        class AestheticHead(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.layers = torch.nn.Sequential(
                    torch.nn.Linear(768, 1024),
                    torch.nn.Dropout(0.2),
                    torch.nn.Linear(1024, 128),
                    torch.nn.Dropout(0.2),
                    torch.nn.Linear(128, 64),
                    torch.nn.Dropout(0.1),
                    torch.nn.Linear(64, 16),
                    torch.nn.Linear(16, 1),
                )

            def forward(self, features):
                return self.layers(features)

        head = AestheticHead()
        head.load_state_dict(payload)
        return head
    state = payload
    if isinstance(payload, dict) and "weight" not in payload:
        for key in ("state_dict", "model"):
            if isinstance(payload.get(key), dict):
                state = payload[key]
                break
    if not isinstance(state, dict) or "weight" not in state or "bias" not in state:
        raise RuntimeError("美學權重格式無法辨識。需要 ViT-L/14 的線性層或 sac+logos MLP。")
    if tuple(state["weight"].shape) != (1, 768):
        raise RuntimeError(f"線性權重維度是 {tuple(state['weight'].shape)}，需要 (1, 768)。")
    layer = torch.nn.Linear(768, 1)
    layer.load_state_dict({"weight": state["weight"].float(), "bias": state["bias"].float()})
    return layer


class ClipAesthetic:
    def __init__(self, weights: Path, device: str, batch_size: int):
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        import open_clip
        import torch

        self.torch = torch
        self.device = device
        self.batch_size = batch_size
        self.note: str | None = None
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14",
            pretrained="laion2b_s32b_b82k",
            device="cpu",
        )
        self.model.eval()
        if device != "cpu":
            self.model = self.model.to(device)
        try:
            payload = torch.load(weights, map_location="cpu", weights_only=True)
        except TypeError:
            payload = torch.load(weights, map_location="cpu")
        self.head = _build_head(torch, payload).to(device).eval()
        if device == "cuda":
            total = torch.cuda.get_device_properties(0).total_memory
            if total < 12 * 1024**3:
                self.note = "顯示記憶體低於 12GB。美學分跑完會先釋放模型，再呼叫 VLM。"

    def score_paths(self, paths: list[Path], names: list[str] | None = None) -> list[tuple[float, bytes | None]]:
        del names
        torch = self.torch
        scored: list[tuple[float, bytes | None]] = []
        for start in range(0, len(paths), self.batch_size):
            chunk = paths[start : start + self.batch_size]
            tensors = []
            for path in chunk:
                with Image.open(path) as image:
                    tensors.append(self.preprocess(image.convert("RGB")))
            batch = torch.stack(tensors).to(self.device)
            with torch.no_grad():
                features = self.model.encode_image(batch)
                features = features / features.norm(dim=-1, keepdim=True)
                features = features.float()
                predicted = self.head(features).squeeze(-1)
            for feature, score in zip(features.detach().cpu(), predicted.detach().cpu()):
                scored.append((float(score), feature.half().numpy().tobytes()))
        return scored

    def close(self) -> None:
        model = getattr(self, "model", None)
        head = getattr(self, "head", None)
        if model is not None:
            del self.model
        if head is not None:
            del self.head
        if self.device == "cuda":
            self.torch.cuda.empty_cache()


def resolve_weights_path(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    candidates: list[Path] = []
    env = os.environ.get("PHOTOCULL_AESTHETIC_WEIGHTS")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "models" / WEIGHT_NAME)
    candidates.append(Path(__file__).resolve().parents[2] / "models" / WEIGHT_NAME)
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_scorer(weights: Path | None, device: str, batch_size: int) -> tuple[ClipAesthetic | None, str | None]:
    weights = resolve_weights_path(weights)
    if weights is None or not Path(weights).exists():
        return None, (
            "未提供美學權重，已略過 CLIP 評分。"
            "散張除明顯失焦外先放覆核；連拍仍用清晰度選代表。"
            "權重可用 --aesthetic-weights 或環境變數 PHOTOCULL_AESTHETIC_WEIGHTS。"
        )
    try:
        import torch
    except Exception:
        return None, '未安裝 PyTorch，已略過美學評分。可在虛擬環境執行 pip install -e ".[gpu]"。'
    try:
        import open_clip  # noqa: F401
    except Exception:
        return None, '未安裝 open_clip，已略過美學評分。可執行 pip install -e ".[gpu]"。'
    resolved = device
    note = None
    if device == "cuda" and not torch.cuda.is_available():
        resolved = "cpu"
        note = "這台沒有可用的 CUDA，美學評分改走 CPU。"
    try:
        scorer = ClipAesthetic(Path(weights), resolved, batch_size)
    except Exception as exc:
        return None, f"美學模型無法載入：{exc}"
    if scorer.note and note:
        note = note + scorer.note
    elif scorer.note:
        note = scorer.note
    return scorer, note
