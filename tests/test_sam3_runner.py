"""別プロセスとして呼ぶ境界（→ `SAM3実装計画.md` 5章 段階5）。

**本物のモデルは動かさない。** 偽の CLI を書いて、アプリの `venv` の python に
実行させる。**プロセスの起動そのものは本物**なので、起動できない・終了コードが
0でない・JSON が壊れている、といった経路が実際に通る。

確かめるのは計画 6章の「SAM境界」の行——正常・候補ゼロ・壊れたJSON・
専用環境不在・実行中の2件目。
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
import threading
import time

import pytest

from manga_layout.ai.sam3.contracts import SegmentationError
from manga_layout.ai.sam3.runner import (
    Sam3Runner,
    SegmentationBusyError,
    python_in,
)


def fake_env(tmp_path: pathlib.Path) -> pathlib.Path:
    """専用環境のふりをするフォルダ。**中身はこの venv の python への写し。**

    本物の SAM 3 環境は 段階0 で作る。ここで要るのは「その場所に python が
    ある」ことだけなので、実行できる python を1つ置けば足りる。
    """
    env_dir = tmp_path / "env"
    target = python_in(env_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 写しではなく、今動いている python をそのまま指す小さな入口を書く
    # （写すと DLL が付いてこず、Windows では動かない）
    target.write_text("", encoding="utf-8")
    return env_dir


class FakeRunner(Sam3Runner):
    """`python_path` だけを、今動いている python に差し替えた `Sam3Runner`。"""

    @property
    def python_path(self) -> pathlib.Path:
        return pathlib.Path(sys.executable)


def write_cli(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """偽の CLI を1つ書く。引数は入力JSONのパス（本物と同じ）。"""
    path = tmp_path / "fake_cli.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


NORMAL_CLI = """
    import json, pathlib, sys, time

    _START = time.perf_counter()

    request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_dir = pathlib.Path(request["out_dir"])
    names = []
    for i in range(2):
        name = f"cand{i}.png"
        (out_dir / name).write_bytes(b"not-a-real-png")
        names.append(name)
    result = {
        "image_px": [120, 80],
        "prompt": request["prompt"],
        "model": "fake-1",
        "candidates": [
            {"index": i, "name": n, "score": 0.9 - i * 0.1, "box": [0, 0, 60, 80]}
            for i, n in enumerate(names)
        ],
        "timings": {
            "startup": 0.0,
            "load": 0.2,
            "infer": 0.3,
            # **子が測れるのは、自分が動き出したあとだけ**（本物も同じ）
            "total": time.perf_counter() - _START,
        },
        "gpu_peak_mb": 1024.0,
    }
    (out_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
"""


@pytest.fixture
def image(tmp_path) -> pathlib.Path:
    path = tmp_path / "src.png"
    path.write_bytes(b"dummy")
    return path


class Test正常に返ってくる:
    def test_候補と時間が受け取れる(self, tmp_path, image):
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, NORMAL_CLI))
        result, out_dir = runner.segment(image, "人物")
        try:
            assert result.found
            assert [c.name for c in result.candidates] == ["cand0.png", "cand1.png"]
            assert (out_dir / "cand0.png").is_file(), "候補は捨ててよい場所に置く"
            assert result.prompt == "人物"
            assert result.model == "fake-1"
        finally:
            runner.discard(out_dir)

    def test_全体の時間は依頼した側で測る(self, tmp_path, image):
        """利用者が待つのは起動を含めた時間（→ `contracts.Timings`）。

        **python の起動そのものが 0.1 秒規模で掛かる。** 子が測れるのは自分が
        動き出したあとだけなので、その差が `startup` に出る。本物では
        モデルのロードが加わって、もっと大きくなるはず（段階4で測る）。
        """
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, NORMAL_CLI))
        result, out_dir = runner.segment(image, "人物")
        try:
            assert result.timings.total > 0.0
            assert result.timings.startup > 0.0, "起動待ちは引き算で出す"
            assert result.timings.startup < result.timings.total
            assert result.timings.load == 0.2, "内訳は子の値のまま"
        finally:
            runner.discard(out_dir)

    def test_片付けると候補が残らない(self, tmp_path, image):
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, NORMAL_CLI))
        _result, out_dir = runner.segment(image, "人物")
        runner.discard(out_dir)
        assert not out_dir.exists(), "作品には何も残さない（→ 段階5の「中止」）"


class Test候補が0件:
    CLI = """
        import json, pathlib, sys

        request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
        out_dir = pathlib.Path(request["out_dir"])
        result = {"image_px": [120, 80], "candidates": [], "prompt": request["prompt"]}
        (out_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    """

    def test_失敗ではなく空の結果(self, tmp_path, image):
        """「見つかりませんでした」と伝えて作品を変えない（→ 計画 3章）。"""
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, self.CLI))
        result, out_dir = runner.segment(image, "存在しないもの")
        try:
            assert result.found is False
            assert result.candidates == ()
        finally:
            runner.discard(out_dir)


class Test読めない結果:
    def test_JSONが壊れていれば断る(self, tmp_path, image):
        cli = write_cli(
            tmp_path,
            """
            import pathlib, sys, json
            request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
            (pathlib.Path(request["out_dir"]) / "result.json").write_text("{壊れた", encoding="utf-8")
            """,
        )
        runner = FakeRunner(fake_env(tmp_path), cli)
        with pytest.raises(SegmentationError, match="読めません"):
            runner.segment(image, "人物")

    def test_結果を書かなければ断る(self, tmp_path, image):
        cli = write_cli(tmp_path, "print('何もしない')")
        runner = FakeRunner(fake_env(tmp_path), cli)
        with pytest.raises(SegmentationError, match="書きませんでした"):
            runner.segment(image, "人物")

    def test_異常終了は理由を添えて断る(self, tmp_path, image):
        cli = write_cli(
            tmp_path,
            """
            import sys
            print("GPU が見つかりません", file=sys.stderr)
            sys.exit(3)
            """,
        )
        runner = FakeRunner(fake_env(tmp_path), cli)
        with pytest.raises(SegmentationError, match="GPU が見つかりません"):
            runner.segment(image, "人物")

    def test_失敗しても置き場を残さない(self, tmp_path, image):
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, "raise SystemExit(1)"))
        before = set(pathlib.Path(tmp_path).parent.glob("sam3-*"))
        with pytest.raises(SegmentationError):
            runner.segment(image, "人物")
        assert not (set(pathlib.Path(tmp_path).parent.glob("sam3-*")) - before)


class Test専用環境が無いとき:
    """**SAM 3 が無いPCでも、アプリは動く**（→ 計画 8章の統合条件3）。"""

    def test_使える状態かを見分けられる(self, tmp_path):
        runner = Sam3Runner(tmp_path / "無い環境", tmp_path / "無いCLI")
        assert runner.available() is False

    def test_頼まれても起動せずに断る(self, tmp_path, image):
        runner = Sam3Runner(tmp_path / "無い環境", tmp_path / "無いCLI")
        with pytest.raises(SegmentationError, match="専用環境"):
            runner.segment(image, "人物")
        assert runner.busy is False, "断ったあとも次を受けられる"

    def test_入口だけ無くても断る(self, tmp_path, image):
        """環境はあるが `cli.py` を置き忘れた場合（段階4の途中で起こりうる）。"""
        runner = FakeRunner(fake_env(tmp_path), tmp_path / "無いCLI")
        assert runner.available() is False
        with pytest.raises(SegmentationError):
            runner.segment(image, "人物")


class Test同時に1件だけ:
    """**待ち行列も作らない**（→ 計画 段階5）。

    「やはり違う」と押し直すたびにプロセスが増えると、GPU メモリを使い切る。
    """

    SLOW_CLI = """
        import json, pathlib, sys, time

        request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
        out_dir = pathlib.Path(request["out_dir"])
        time.sleep(1.0)
        result = {"image_px": [10, 10], "candidates": [], "prompt": request["prompt"]}
        (out_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    """

    def test_実行中の2件目は起動しない(self, tmp_path, image):
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, self.SLOW_CLI))
        errors: list[Exception] = []
        results: list[object] = []

        def 先の1件():
            try:
                results.append(runner.segment(image, "人物"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        worker = threading.Thread(target=先の1件)
        worker.start()
        try:
            # 走り出すまで待つ。**決め打ちの秒数で待たない**——遅い機械で
            # 落ちるテストになる。`busy` が立つのを見る
            while not runner.busy and worker.is_alive():
                time.sleep(0.01)
            with pytest.raises(SegmentationBusyError):
                runner.segment(image, "髪")
        finally:
            worker.join(timeout=10)

        assert not errors, f"1件目は普通に終わる: {errors}"
        assert len(results) == 1
        runner.discard(results[0][1])

    def test_終わればまた受けられる(self, tmp_path, image):
        runner = FakeRunner(fake_env(tmp_path), write_cli(tmp_path, NORMAL_CLI))
        for _ in range(2):
            _result, out_dir = runner.segment(image, "人物")
            runner.discard(out_dir)
        assert runner.busy is False
