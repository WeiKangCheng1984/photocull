from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

ALLOWED_DEFECTS = {
    "closed_eyes",
    "blink",
    "motion_blur",
    "out_of_focus",
    "cut_off_subject",
    "awkward_crop",
    "blown_highlights",
    "underexposed",
    "distracting_object",
}
ALLOWED_RECOMMEND = {"keep", "review", "reject"}

PROMPT = """你在協助整理照片。只回傳一個 JSON 物件，不要使用 markdown。
格式：
{"scene":["portrait"],"description":"一句繁體中文","defects":[],"recommend":"keep"}
defects 只能從以下選擇，沒有就空陣列：closed_eyes, blink, motion_blur, out_of_focus, cut_off_subject, awkward_crop, blown_highlights, underexposed, distracting_object
recommend 只能是 keep、review 或 reject。
"""


def ollama_limits(num_ctx: int | None) -> dict | None:
    if not num_ctx:
        return None
    return {"num_ctx": num_ctx, "num_predict": 256}


def parse_vlm_text(text: str) -> dict | None:
    raw = text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scene = data.get("scene") or []
    if isinstance(scene, str):
        scene = [scene]
    if not isinstance(scene, list):
        scene = []
    defects = data.get("defects") or []
    if not isinstance(defects, list):
        defects = []
    recommend = data.get("recommend")
    if recommend not in ALLOWED_RECOMMEND:
        recommend = None
    description = data.get("description")
    return {
        "scene": [str(item) for item in scene if str(item).strip()],
        "description": "" if description is None else str(description).strip(),
        "defects": [item for item in defects if item in ALLOWED_DEFECTS],
        "recommend": recommend,
    }


class VlmClient:
    def __init__(self, model: str, base_url: str | None = None, num_ctx: int | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else None
        self.num_ctx = num_ctx
        self.dead = False

    def available(self) -> tuple[bool, str]:
        if self.base_url:
            try:
                with urllib.request.urlopen(self.base_url + "/v1/models", timeout=5):
                    return True, ""
            except Exception as exc:
                return False, f"無法連線 vLLM（{self.base_url}）：{exc}"
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            return False, f"Ollama 未啟動或沒有模型 {self.model}，已略過語意分析。"
        names = [str(item.get("name", "")) for item in payload.get("models", [])]
        if any(name == self.model or name.startswith(self.model) for name in names):
            return True, ""
        return False, (
            f"Ollama 沒有模型 {self.model}，已略過語意分析。"
            f"安裝後可重跑：ollama pull {self.model}"
        )

    def describe(self, path: Path) -> dict:
        if self.dead:
            return {"status": "vlm_skipped", "scene": [], "description": "", "defects": [], "recommend": None}
        image = base64.b64encode(path.read_bytes()).decode("ascii")
        for _ in range(2):
            try:
                text = self._request(image)
            except (urllib.error.URLError, TimeoutError, OSError):
                self.dead = True
                return {"status": "vlm_skipped", "scene": [], "description": "", "defects": [], "recommend": None}
            parsed = parse_vlm_text(text)
            if parsed is not None:
                parsed["status"] = "ok"
                return parsed
        return {"status": "vlm_error", "scene": [], "description": "", "defects": [], "recommend": None}

    def _request(self, image_b64: str) -> str:
        if self.base_url:
            url = self.base_url + "/v1/chat/completions"
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
                        ],
                    }
                ],
            }
        else:
            url = "http://127.0.0.1:11434/api/chat"
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [{"role": "user", "content": PROMPT, "images": [image_b64]}],
            }
            if self.num_ctx:
                payload["options"] = ollama_limits(self.num_ctx)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        if self.base_url:
            return str(body["choices"][0]["message"]["content"])
        return str(body["message"]["content"])
