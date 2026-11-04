from __future__ import annotations

import re

from photocull.models import BUCKET_LABEL

_LINE = re.compile(r"^\s*(?P<label>[^：\n]+)：(?P<count>\d+)\s*$")
_LABEL_TO_BUCKET = {label: bucket for bucket, label in BUCKET_LABEL.items()}
_REJECT_PREFIX = ("失焦", "美學偏低", "缺陷", "完全重複", "近重複", "連拍落選")


def parse_report_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        matched = _LINE.match(line)
        if matched is None:
            continue
        label = matched.group("label")
        count = int(matched.group("count"))
        bucket = _LABEL_TO_BUCKET.get(label)
        if bucket is not None:
            counts[bucket] = count
        elif label == "硬連結失敗":
            counts["link_failures"] = count
    return counts


def result_badge(text: str) -> str:
    counts = parse_report_counts(text)
    if not counts:
        return ""
    reject = sum(counts.get(bucket, 0) for bucket in BUCKET_LABEL if bucket.startswith("reject_"))
    parts = [
        f"保留 {counts.get('keep', 0)}",
        f"覆核 {counts.get('review', 0)}",
        f"廢片 {reject}",
    ]
    failures = counts.get("link_failures", 0)
    if failures:
        parts.append(f"連結失敗 {failures}")
    return " · ".join(parts)


def line_tag(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("保留"):
        return "keep"
    if stripped.startswith("覆核"):
        return "review"
    if stripped.startswith(_REJECT_PREFIX):
        return "reject"
    if stripped.startswith("硬連結失敗"):
        matched = _LINE.match(line)
        if matched is not None and int(matched.group("count")) > 0:
            return "fail"
    return "plain"
