"""AI 提供者との境界（要件定義 10.3 / `SAM3実装計画.md` 4章）。

**モデルもGPUも要らない。** ここで確かめるのは「渡す形・返る形」だけで、
本物の SAM 3 を動かす確認は別コマンドに分けてある（→ 計画 6章）。
偽の提供者を置くのは、切り抜きの品質と、アプリ側の受け方を分けて見るため。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from manga_layout.ai.sam3.contracts import (
    MaskCandidate,
    SegmentationError,
    SegmentationRequest,
    SegmentationResult,
    Timings,
)
from manga_layout.image_masks import decode_mask, is_binary
from manga_layout.images import size_px, to_png_bytes


def mask_png(width: int, height: int, keep_left: int) -> bytes:
    """左から `keep_left` 画素だけ残す白黒マスク。"""
    mask = QImage(width, height, QImage.Format.Format_Grayscale8)
    mask.fill(Qt.GlobalColor.black)
    for x in range(keep_left):
        for y in range(height):
            mask.setPixelColor(x, y, QColor(255, 255, 255))
    return to_png_bytes(mask)


class FakeProvider:
    """偽のAI提供者。**候補マスクPNGと結果を、本物と同じ形で返す。**

    本物との違いは中身の作り方だけ（左からいくつ残すかを決め打ちする）。
    形が同じなので、アプリ側の受け方はここで確かめきれる。
    """

    def __init__(self, image_px: tuple[int, int], keeps: list[int]) -> None:
        self.image_px = image_px
        self.keeps = keeps
        self.calls: list[SegmentationRequest] = []

    def segment(self, request: SegmentationRequest) -> SegmentationResult:
        self.calls.append(request)
        request.out_dir.mkdir(parents=True, exist_ok=True)
        width, height = self.image_px
        candidates = []
        for i, keep in enumerate(self.keeps[: request.max_candidates]):
            name = f"cand{i}.png"
            (request.out_dir / name).write_bytes(mask_png(width, height, keep))
            candidates.append(
                MaskCandidate(
                    index=i,
                    name=name,
                    score=1.0 - i * 0.1,
                    box=(0, 0, keep, height),
                )
            )
        return SegmentationResult(
            image_px=self.image_px,
            candidates=tuple(candidates),
            prompt=request.prompt,
            model="fake-1",
            timings=Timings(startup=0.1, load=0.2, infer=0.3, total=0.7),
            gpu_peak_mb=None,
        )


@pytest.fixture
def request_for(tmp_path) -> SegmentationRequest:
    return SegmentationRequest(
        image_path=tmp_path / "src.png",
        prompt="人物",
        out_dir=tmp_path / "out",
    )


class Test偽の提供者:
    def test_候補ごとにマスクPNGが返る(self, request_for, qapp):
        provider = FakeProvider((120, 80), [40, 90])
        result = provider.segment(request_for)

        assert result.found
        assert len(result.candidates) == 2
        for candidate in result.candidates:
            mask = decode_mask(candidate.path_in(request_for.out_dir).read_bytes())
            assert size_px(mask) == (120, 80), "マスクは元画像と同じ寸法（→ 4.2）"
            assert is_binary(mask), "最初の実装が作るのは白と黒だけ"

    def test_候補が0件でも失敗ではない(self, request_for, qapp):
        """「見つかりませんでした」と伝えて作品を変えないのが正しい終わり方（→ 3章）。"""
        result = FakeProvider((120, 80), []).segment(request_for)
        assert result.found is False
        assert result.candidates == ()

    def test_上限を超えて返さない(self, tmp_path, qapp):
        request = SegmentationRequest(
            image_path=tmp_path / "src.png",
            prompt="髪",
            out_dir=tmp_path / "out",
            max_candidates=2,
        )
        result = FakeProvider((60, 60), [10, 20, 30, 40]).segment(request)
        assert len(result.candidates) == 2


class Test結果の受け渡し:
    """**やり取りは入力JSON・出力JSON・候補PNGだけ**（→ 計画 5章 段階5）。"""

    def test_JSONを往復しても同じ内容(self, request_for, qapp, tmp_path):
        result = FakeProvider((120, 80), [40, 90]).segment(request_for)
        path = tmp_path / "result.json"
        path.write_text(json.dumps(result.to_dict()), encoding="utf-8")

        back = SegmentationResult.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        assert back == result

    def test_依頼もJSONを往復できる(self, request_for):
        back = SegmentationRequest.from_dict(request_for.to_dict())
        assert back == request_for

    def test_パスは区切りを揃えて書く(self, request_for):
        """Windows の区切りをそのまま入れない（→ `SegmentationRequest.to_dict`）。"""
        written = request_for.to_dict()["image"]
        assert "/" in written or written == request_for.image_path.name
        assert chr(92) not in written


class Test壊れた結果:
    """**読めないものは読めないと言う。** 黙って空の結果にしない。"""

    def test_辞書でなければ断る(self):
        with pytest.raises(SegmentationError):
            SegmentationResult.from_dict([1, 2, 3])

    def test_画素数が無ければ断る(self):
        with pytest.raises(SegmentationError):
            SegmentationResult.from_dict({"candidates": []})

    def test_画素数が0以下なら断る(self):
        with pytest.raises(SegmentationError):
            SegmentationResult.from_dict({"image_px": [0, 10], "candidates": []})

    def test_候補の中身が壊れていれば断る(self):
        with pytest.raises(SegmentationError):
            SegmentationResult.from_dict(
                {"image_px": [10, 10], "candidates": [{"index": 0, "name": "a.png"}]}
            )

    def test_フォルダの外を指す名前は断る(self):
        """壊れたJSONでも、読む先が別のフォルダへ滑らないようにする。"""
        for name in ("../secret.png", "sub/a.png", "", "."):
            with pytest.raises(SegmentationError):
                SegmentationResult.from_dict(
                    {
                        "image_px": [10, 10],
                        "candidates": [
                            {"index": 0, "name": name, "score": 1.0, "box": [0, 0, 1, 1]}
                        ],
                    }
                )

    def test_時間の内訳が無くても読める(self):
        """古い出力・簡素な提供者でも、結果そのものは受け取れる。"""
        result = SegmentationResult.from_dict({"image_px": [10, 10]})
        assert result.timings == Timings()
        assert result.gpu_peak_mb is None


def test_マスクの在り処は受け取った側が組み立てる(tmp_path):
    candidate = MaskCandidate(index=0, name="cand0.png", score=0.5, box=(0, 0, 1, 1))
    assert candidate.path_in(tmp_path) == tmp_path / "cand0.png"
    assert isinstance(candidate.path_in(pathlib.Path(".")), pathlib.Path)
