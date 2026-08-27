"""専用環境の中で動く入口の、モデル以外の部分（→ `SAM3実装計画.md` 段階4）。

**モデルを呼ぶ所（`_predict`）は差し替えて確かめる。** アプリの `venv` には
PyTorch も SAM 3 も入れないので、ここで動かせるのは PNG の書き出し・出力JSON・
候補の上限まで。**実モデルを通した確認は別（段階4で実機に対して行う）。**
"""

from __future__ import annotations

import json
import pathlib

import pytest

from manga_layout.ai.sam3 import cli
from manga_layout.ai.sam3.contracts import SegmentationRequest, SegmentationResult
from manga_layout.image_masks import decode_mask, is_binary
from manga_layout.images import size_px


def prediction(width=8, height=4, keep=4, score=0.9) -> cli.Prediction:
    """左から `keep` 画素だけ 255 のマスク。"""
    row = bytes([255] * keep + [0] * (width - keep))
    return cli.Prediction(
        mask=row * height, width=width, height=height, score=score, box=(0, 0, keep, height)
    )


@pytest.fixture
def request_for(tmp_path) -> SegmentationRequest:
    return SegmentationRequest(
        image_path=tmp_path / "src.png", prompt="人物", out_dir=tmp_path / "out"
    )


class TestPNGの書き出し:
    def test_8bitグレースケールとして読める(self, tmp_path, qapp):
        p = prediction()
        path = tmp_path / "mask.png"
        cli.write_gray_png(path, p.width, p.height, p.mask)

        mask = decode_mask(path.read_bytes())
        assert size_px(mask) == (8, 4)
        assert is_binary(mask), "最初の実装が作るのは白と黒だけ（→ 4.2）"

    def test_濃淡がそのまま残る(self, tmp_path, qapp):
        from PySide6.QtGui import qGray

        cli.write_gray_png(tmp_path / "m.png", 4, 1, bytes([0, 64, 128, 255]))
        mask = decode_mask((tmp_path / "m.png").read_bytes())
        assert [qGray(mask.pixel(x, 0)) for x in range(4)] == [0, 64, 128, 255]

    def test_画素数が合わなければ断る(self, tmp_path):
        with pytest.raises(ValueError):
            cli.write_gray_png(tmp_path / "m.png", 4, 4, bytes([255] * 3))


class Test結果の書き出し:
    def test_候補ごとにPNGとJSONが出る(self, request_for, monkeypatch, qapp):
        monkeypatch.setattr(
            cli,
            "_predict",
            lambda r: cli.Outcome((8, 4), [prediction(keep=2), prediction(keep=6)], 1.5),
        )
        assert cli.main(["cli.py", str(_written(request_for))]) == 0

        data = json.loads(
            (request_for.out_dir / cli.RESULT_FILENAME).read_text(encoding="utf-8")
        )
        result = SegmentationResult.from_dict(data)
        assert [c.name for c in result.candidates] == ["cand0.png", "cand1.png"]
        assert result.image_px == (8, 4)
        assert result.prompt == "人物"
        assert result.timings.total > 0.0
        for candidate in result.candidates:
            assert candidate.path_in(request_for.out_dir).is_file()

    def test_候補が0件でも結果を書く(self, request_for, monkeypatch, qapp):
        """書かないと、頼んだ側から「落ちた」のと区別が付かない（→ `runner`）。"""
        monkeypatch.setattr(cli, "_predict", lambda r: cli.Outcome((120, 80), []))
        assert cli.main(["cli.py", str(_written(request_for))]) == 0

        result = SegmentationResult.from_dict(
            json.loads((request_for.out_dir / cli.RESULT_FILENAME).read_text(encoding="utf-8"))
        )
        assert result.candidates == ()
        assert result.image_px == (120, 80), (
            "候補が0件でも元画像の寸法は分かる。0×0 だと受け取る側が読めない"
        )

    def test_上限を超えて書かない(self, tmp_path, monkeypatch, qapp):
        request = SegmentationRequest(
            image_path=tmp_path / "src.png",
            prompt="髪",
            out_dir=tmp_path / "out",
            max_candidates=2,
        )
        monkeypatch.setattr(cli, "_predict", lambda r: cli.Outcome((8, 4), [prediction()] * 5))
        assert cli.main(["cli.py", str(_written(request))]) == 0

        pngs = sorted(p.name for p in request.out_dir.glob("*.png"))
        assert pngs == ["cand0.png", "cand1.png"]

    def test_引数が足りなければ断る(self):
        assert cli.main(["cli.py"]) == 2


class Test専用環境を持ち込まない:
    """**アプリの `venv` から読めること。** ここが崩れると、この試験自体が動かない。"""

    def test_読み込むだけではtorchを引き込まない(self):
        """`torch` も `sam3` も、`_predict` の中でだけ import する。

        module の先頭で import すると、アプリ側のテストが専用環境無しでは
        collect すらできなくなる（このファイルが現に動いていることが証拠だが、
        書き換えで崩れやすいので明示しておく）。
        """
        import sys

        assert "manga_layout.ai.sam3.cli" in sys.modules
        assert "torch" not in sys.modules
        assert "sam3" not in sys.modules

    def test_モデルを読む前はリビジョンが空(self):
        """実験ログに「どの版で出た結果か」を残すためのもの（→ 4.5）。"""
        assert cli.model_revision() == ""

    def test_GPUを測れない環境ではNoneを返す(self):
        """0 と答えると「使っていない」のか「測っていない」のか分からなくなる。"""
        peak = cli.gpu_peak_mb()
        assert peak is None or peak >= 0.0


def _written(request: SegmentationRequest) -> pathlib.Path:
    """依頼を JSON に書き出して、そのパスを返す（本物と同じ渡し方）。"""
    request.out_dir.mkdir(parents=True, exist_ok=True)
    path = request.out_dir / "request.json"
    path.write_text(json.dumps(request.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path


def test_元画像と寸法の違う候補は書かない(request_for, monkeypatch, qapp):
    """**適用のときではなく、作るときに断る。**

    通してしまうと、利用者が候補を見比べて選んだあとで断られることになる
    （→ `EditorState.apply_image_mask`）。
    """
    monkeypatch.setattr(
        cli,
        "_predict",
        lambda r: cli.Outcome((8, 4), [cli.Prediction(bytes(4), 2, 2, 0.5, (0, 0, 2, 2))]),
    )
    with pytest.raises(ValueError, match="寸法"):
        cli.main(["cli.py", str(_written(request_for))])
