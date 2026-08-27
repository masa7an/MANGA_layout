"""SAM 3 実験ログ（→ `SAM3実装計画.md` 4.5）。

**作品を表示するために要らない情報は `project.json` に入れない。** 一方で、
実験中に「どの文でどの結果になったか」を失うと、採用・見送りの判断ができない。
そこで作品フォルダの `sam3_experiment_log.json` に別途書く。

- **マスクを使わない作品には作らない。** 使っていない人のフォルダに、
  この実験の痕跡を残さない
- **書けなくても作品の保存を壊さない。** ログは実験の記録であって、作品の
  一部ではない。壊れたログは無かったことにして読み進む
- **書かないもの: 認証トークン・元画像の絶対パス・画像そのもの。**
  フォルダごと人に渡せる状態を保つ

記録は2回に分けて書く。候補を受け取った時点で1行足し、利用者が適用した
時点でその行を更新する（→ `mark_applied`）。適用しなかった推論も残るので、
「何度やり直したか」まで後から数えられる。
"""

from __future__ import annotations

import datetime
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

from ...storage import atomic_write_text
from .contracts import SegmentationResult, Timings

LOG_FILENAME = "sam3_experiment_log.json"

# 1回の推論がどう終わったか
STATUS_FOUND = "found"  # 候補が返った（まだ適用していない）
STATUS_NONE = "none"  # 候補が0件だった。**失敗ではない**
STATUS_FAILED = "failed"  # 起動できない・結果が読めない
STATUS_APPLIED = "applied"  # 候補を1つ適用した
STATUS_IGNORED = "ignored"  # 結果を使わなかった（→ 計画 段階5の「中止」）


def now_text() -> str:
    """記録用の日時（秒まで）。**時計を読むのはここだけ。**

    受け取る側が文字列で持つのは、後から表にするときに並べ替えるのが
    そのままでできるため。テストは時計を読まずに好きな値を渡せる。
    """
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class CandidateRecord:
    """候補1つぶんの記録。**マスクの中身は書かない**（名前と数字だけ）。"""

    index: int
    score: float
    box: tuple[int, int, int, int]

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "score": self.score, "box": list(self.box)}

    @classmethod
    def from_dict(cls, data: Any) -> CandidateRecord:
        box = data.get("box") or [0, 0, 0, 0]
        return cls(
            index=int(data.get("index", 0)),
            score=float(data.get("score", 0.0)),
            box=(int(box[0]), int(box[1]), int(box[2]), int(box[3])),
        )


@dataclass(frozen=True)
class Entry:
    """1回の推論の記録。

    `run_id` は依頼した側が付ける通し番号。**適用したときに同じ行を更新する**
    ので、これが無いと「どの推論を採用したか」が結び付かない。
    """

    run_id: str
    at: str
    image_id: str
    prompt: str
    status: str
    model: str = ""
    image_px: tuple[int, int] = (0, 0)
    candidates: tuple[CandidateRecord, ...] = ()
    # 採用した候補の番号と、そのとき保存したマスク。適用するまでは無い
    chosen: int | None = None
    mask_asset: str = ""
    timings: Timings = field(default_factory=Timings)
    gpu_peak_mb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "at": self.at,
            "image_id": self.image_id,
            "prompt": self.prompt,
            "status": self.status,
            "model": self.model,
            "image_px": [self.image_px[0], self.image_px[1]],
            "candidates": [c.to_dict() for c in self.candidates],
            "chosen": self.chosen,
            "mask_asset": self.mask_asset,
            "timings": self.timings.to_dict(),
            "gpu_peak_mb": self.gpu_peak_mb,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Entry:
        """**読めない行は捨てる側で拾う**（→ `read_log`）。ここは素直に組み立てる。"""
        px = data.get("image_px") or [0, 0]
        raw = data.get("candidates") or []
        chosen = data.get("chosen")
        peak = data.get("gpu_peak_mb")
        return cls(
            run_id=str(data["run_id"]),
            at=str(data.get("at", "")),
            image_id=str(data.get("image_id", "")),
            prompt=str(data.get("prompt", "")),
            status=str(data.get("status", "")),
            model=str(data.get("model", "")),
            image_px=(int(px[0]), int(px[1])),
            candidates=tuple(CandidateRecord.from_dict(item) for item in raw),
            chosen=None if chosen is None else int(chosen),
            mask_asset=str(data.get("mask_asset", "")),
            timings=Timings.from_dict(data.get("timings"), "timings"),
            gpu_peak_mb=None if peak is None else float(peak),
        )


def entry_for(
    result: SegmentationResult, *, run_id: str, image_id: str, at: str | None = None
) -> Entry:
    """推論の結果から記録を1つ作る。**候補が0件なら `none`。**"""
    return Entry(
        run_id=run_id,
        at=at or now_text(),
        image_id=image_id,
        prompt=result.prompt,
        status=STATUS_FOUND if result.found else STATUS_NONE,
        model=result.model,
        image_px=result.image_px,
        candidates=tuple(
            CandidateRecord(index=c.index, score=c.score, box=c.box)
            for c in result.candidates
        ),
        timings=result.timings,
        gpu_peak_mb=result.gpu_peak_mb,
    )


def log_path(project_dir: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(project_dir) / LOG_FILENAME


def read_log(project_dir: pathlib.Path | str) -> list[Entry]:
    """記録を読む。**無ければ空。壊れていても空。**

    ここで例外を投げると、ログ1つで作品が開けなくなる。実験の記録は
    作品の一部ではないので、読めないものは無かったことにする
    （→ 計画 段階1〜3「ログが壊れても作品を開ける」）。
    """
    path = log_path(project_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("runs")
    if not isinstance(raw, list):
        return []

    entries: list[Entry] = []
    for item in raw:
        # **1行ずつ拾う。** 1行壊れているだけで、その前後まで捨てない
        if not isinstance(item, dict) or "run_id" not in item:
            continue
        try:
            entries.append(Entry.from_dict(item))
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def write_log(project_dir: pathlib.Path | str, entries: list[Entry]) -> bool:
    """記録を書き切る。書けたら True。**失敗しても例外にしない。**

    ログの書き込みは作品の保存とは別物で、こちらが失敗しても作品や
    マスクの保存を壊してはいけない（→ 計画 4.5）。
    """
    text = json.dumps(
        {"app": "MANGA_layout", "kind": "sam3_experiment_log", "runs": [e.to_dict() for e in entries]},
        ensure_ascii=False,
        indent=2,
    )
    try:
        atomic_write_text(log_path(project_dir), text + "\n")
    except OSError:
        return False
    return True


def append_entry(project_dir: pathlib.Path | str, entry: Entry) -> bool:
    """記録を1行足す。同じ `run_id` があれば置き換える。"""
    entries = [e for e in read_log(project_dir) if e.run_id != entry.run_id]
    entries.append(entry)
    return write_log(project_dir, entries)


def mark_applied(
    project_dir: pathlib.Path | str, run_id: str, *, chosen: int, mask_asset: str
) -> bool:
    """採用した候補とマスクを、その推論の行に書き足す。

    **推論の記録と別の行にしない。** 別にすると、1回の推論に対する
    「返ってきたもの」と「選んだもの」を、後から突き合わせる作業が要る。
    """
    return _update(project_dir, run_id, status=STATUS_APPLIED, chosen=chosen, mask_asset=mask_asset)


def mark_status(project_dir: pathlib.Path | str, run_id: str, status: str) -> bool:
    """終わり方だけを書き換える（結果を無視した・失敗した）。"""
    return _update(project_dir, run_id, status=status)


def _update(
    project_dir: pathlib.Path | str,
    run_id: str,
    *,
    status: str,
    chosen: int | None = None,
    mask_asset: str | None = None,
) -> bool:
    entries = read_log(project_dir)
    found = False
    updated: list[Entry] = []
    for entry in entries:
        if entry.run_id != run_id:
            updated.append(entry)
            continue
        found = True
        updated.append(
            Entry(
                run_id=entry.run_id,
                at=entry.at,
                image_id=entry.image_id,
                prompt=entry.prompt,
                status=status,
                model=entry.model,
                image_px=entry.image_px,
                candidates=entry.candidates,
                chosen=entry.chosen if chosen is None else chosen,
                mask_asset=entry.mask_asset if mask_asset is None else mask_asset,
                timings=entry.timings,
                gpu_peak_mb=entry.gpu_peak_mb,
            )
        )
    if not found:
        return False
    return write_log(project_dir, updated)
