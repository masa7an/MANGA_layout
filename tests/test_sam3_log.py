"""SAM 3 実験ログ（要件定義 10.3 / `SAM3実装計画.md` 4.5）。

**作品の一部ではない。** ここで確かめるのは「記録が残ること」と、
**壊れても作品の側を巻き込まないこと**の2つ。
"""

from __future__ import annotations

import json

from manga_layout.ai.sam3.contracts import MaskCandidate, SegmentationResult, Timings
from manga_layout.ai.sam3.experiment_log import (
    LOG_FILENAME,
    STATUS_APPLIED,
    STATUS_FOUND,
    STATUS_IGNORED,
    STATUS_NONE,
    append_entry,
    entry_for,
    log_path,
    mark_applied,
    mark_status,
    read_log,
)


def result(candidates: int = 2) -> SegmentationResult:
    return SegmentationResult(
        image_px=(120, 80),
        candidates=tuple(
            MaskCandidate(index=i, name=f"cand{i}.png", score=0.9 - i * 0.1, box=(0, 0, 60, 80))
            for i in range(candidates)
        ),
        prompt="人物",
        model="fake-1",
        timings=Timings(startup=0.5, load=2.0, infer=1.5, total=4.2),
        gpu_peak_mb=1234.5,
    )


class Test記録:
    def test_推論を1行残す(self, tmp_path):
        entry = entry_for(result(), run_id="r1", image_id="img1", at="2026-08-27T10:00:00")
        assert append_entry(tmp_path, entry)

        entries = read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0].prompt == "人物"
        assert entries[0].status == STATUS_FOUND
        assert entries[0].chosen is None, "適用するまでは採用候補が無い"
        assert [c.score for c in entries[0].candidates] == [0.9, 0.8]
        assert entries[0].timings.total == 4.2
        assert entries[0].gpu_peak_mb == 1234.5

    def test_候補が0件でも残す(self, tmp_path):
        """「その文では見つからなかった」ことも実験の結果（→ 4.5）。"""
        append_entry(tmp_path, entry_for(result(0), run_id="r1", image_id="img1"))
        assert read_log(tmp_path)[0].status == STATUS_NONE

    def test_採用したら同じ行に書き足す(self, tmp_path):
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        assert mark_applied(tmp_path, "r1", chosen=1, mask_asset="assets/abc.png")

        entries = read_log(tmp_path)
        assert len(entries) == 1, "別の行を作らない"
        assert entries[0].status == STATUS_APPLIED
        assert entries[0].chosen == 1
        assert entries[0].mask_asset == "assets/abc.png"
        assert entries[0].prompt == "人物", "推論のときの中身は残る"

    def test_使わなかった結果も残す(self, tmp_path):
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        assert mark_status(tmp_path, "r1", STATUS_IGNORED)
        assert read_log(tmp_path)[0].status == STATUS_IGNORED

    def test_知らない推論は書き換えない(self, tmp_path):
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        assert mark_applied(tmp_path, "r9", chosen=0, mask_asset="assets/x.png") is False
        assert read_log(tmp_path)[0].status == STATUS_FOUND

    def test_複数回ぶんが並ぶ(self, tmp_path):
        for i in range(3):
            append_entry(tmp_path, entry_for(result(), run_id=f"r{i}", image_id="img1"))
        assert [e.run_id for e in read_log(tmp_path)] == ["r0", "r1", "r2"]


class Test作品を巻き込まない:
    def test_使わない作品には作らない(self, tmp_path):
        """マスクを使っていない人のフォルダに、この実験の痕跡を残さない。"""
        assert read_log(tmp_path) == []
        assert not log_path(tmp_path).exists()

    def test_壊れていても空として読む(self, tmp_path):
        (tmp_path / LOG_FILENAME).write_text("{壊れた", encoding="utf-8")
        assert read_log(tmp_path) == []

    def test_壊れた行だけを飛ばす(self, tmp_path):
        """1行壊れているだけで、前後の記録まで捨てない。"""
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        data = json.loads(log_path(tmp_path).read_text(encoding="utf-8"))
        data["runs"].append({"at": "2026-08-27T11:00:00"})  # run_id が無い
        data["runs"].append({"run_id": "r2", "image_px": "壊れている"})
        log_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")

        entries = read_log(tmp_path)
        assert [e.run_id for e in entries] == ["r1"]

    def test_書き足しても前の行が残る(self, tmp_path):
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        append_entry(tmp_path, entry_for(result(0), run_id="r2", image_id="img2"))
        assert len(read_log(tmp_path)) == 2

    def test_書いた中身に絶対パスも画像も入らない(self, tmp_path):
        """フォルダごと人へ渡せる状態を保つ（→ 4.5）。"""
        append_entry(tmp_path, entry_for(result(), run_id="r1", image_id="img1"))
        text = log_path(tmp_path).read_text(encoding="utf-8")
        assert "cand0.png" not in text, "候補の在り処は捨ててよい場所の中の話"
        assert str(tmp_path) not in text
