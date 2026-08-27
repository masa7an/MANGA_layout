"""専用環境の Python を別プロセスとして呼ぶ（→ `SAM3実装計画.md` 5章 段階5）。

**アプリの `venv` に PyTorch も SAM 3 も入れない。** モデルは `ai/sam3/.venv/`
の専用環境で動かし、やり取りは**入力JSON・出力JSON・候補マスクPNG**の3つだけ。
この形を保つ限り、あとで常駐ワーカーへ替えても画面・保存・描画は変わらない。

**同時に走らせるのは常に1件だけ。** 実行中の2件目は起動せず、待ち行列も作らない
（→ 計画 段階5）。「やはり違う」と押し直すたびにプロセスが増えると、GPU メモリを
使い切る経路ができる。

**ここは画面を知らない。** `segment()` は終わるまで戻らないので、呼ぶ側が別スレッドで
動かす。逆に、**画面を止めない責任はここでは負えない**（Qt を持ち込まないため）。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .contracts import (
    SegmentationError,
    SegmentationRequest,
    SegmentationResult,
    Timings,
)

# 専用環境の置き場（Git管理外 → 計画 段階0）
ENV_DIR = pathlib.Path(__file__).parent / ".venv"
# 専用環境の中でだけ動く入口
CLI_PATH = pathlib.Path(__file__).parent / "cli.py"
# リポジトリの根。子プロセスが `manga_layout.ai.sam3.contracts` を読めるようにする
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 出力JSONの名前。入力JSONと同じ場所に置く
REQUEST_FILENAME = "request.json"
RESULT_FILENAME = "result.json"


class SegmentationBusyError(SegmentationError):
    """既に1件走っているのに、もう1件頼まれた。

    **失敗ではなく「今はできない」。** 画面はこれを受けて待機中だと示すだけで、
    作品は何も変わらない。型を分けてあるのは、利用者へ伝えることが違うため
    （`SegmentationError` のほうは「使える状態になっていない」）。

    **それでも `SegmentationError` の一種にしてある。** 受け損ねた例外は
    Qt のスロットを突き抜け、画面には何も出ないまま操作だけが効かなくなる
    （→ `EditorState._edit_slant` の注記）。まとめて受ける側で取りこぼさない
    ほうを選んだ。
    """


def python_in(env_dir: pathlib.Path) -> pathlib.Path:
    """その環境の python の在り処。**Windows だけ場所が違う。**"""
    if sys.platform == "win32":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


class Sam3Runner:
    """SAM 3 を別プロセスとして1件ずつ呼ぶ。

    `env_dir` と `cli_path` を差し替えられるようにしてあるのは、**偽の CLI で
    境界を確かめるため**（→ tests/test_sam3_runner.py）。本物のモデルを
    落とさずに、正常・候補ゼロ・壊れたJSON・専用環境不在・2件目の拒否まで
    確かめられる。
    """

    def __init__(
        self,
        env_dir: pathlib.Path | None = None,
        cli_path: pathlib.Path | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self.env_dir = pathlib.Path(env_dir) if env_dir is not None else ENV_DIR
        self.cli_path = pathlib.Path(cli_path) if cli_path is not None else CLI_PATH
        # 待ち切る上限（秒）。None は待ち続ける。**既定では上限を置かない**
        # ——モデルの初回ロードがどれだけ掛かるかを段階4で測る前に、
        # 当てずっぽうの上限で切ると、測りたいものが測れない
        self.timeout = timeout
        self._lock = threading.Lock()
        self._busy = False

    # -- 使える状態か ------------------------------------------------------

    @property
    def python_path(self) -> pathlib.Path:
        return python_in(self.env_dir)

    def available(self) -> bool:
        """専用環境と入口がそろっているか。

        **無くてもアプリは動く**（→ 計画 8章の統合条件3）。画面はこれを見て
        項目をグレーにするか、押されたときに作り方を案内する。
        """
        return self.python_path.is_file() and self.cli_path.is_file()

    @property
    def busy(self) -> bool:
        return self._busy

    # -- 1件だけ走らせる ---------------------------------------------------

    def segment(
        self, image_path: pathlib.Path, prompt: str, *, max_candidates: int = 8
    ) -> tuple[SegmentationResult, pathlib.Path]:
        """切り抜きを1件頼む。**終わるまで戻らない。**

        返すのは結果と、候補マスクPNGの置き場。置き場は**捨ててよい場所**
        （一時フォルダ）で、使い終わったら `discard()` で片付ける。適用した
        マスクは呼ぶ側が `assets/` へ入れる（→ `EditorState.apply_image_mask`）。

        実行中に呼ばれたら `SegmentationBusyError`。**待ち行列は作らない。**
        """
        with self._lock:
            if self._busy:
                raise SegmentationBusyError(
                    "切り抜きを実行中です。終わってからもう一度お試しください"
                )
            if not self.available():
                raise SegmentationError(
                    f"SAM 3 の専用環境が見つかりません（{self.env_dir}）。"
                    "先に専用環境を作ってください"
                )
            self._busy = True

        out_dir = pathlib.Path(tempfile.mkdtemp(prefix="sam3-"))
        try:
            return self._run(image_path, prompt, out_dir, max_candidates)
        except Exception:
            # 途中で終わったぶんの候補は残さない。**作品には触れていない**
            self.discard(out_dir)
            raise
        finally:
            self._busy = False

    def _run(
        self,
        image_path: pathlib.Path,
        prompt: str,
        out_dir: pathlib.Path,
        max_candidates: int,
    ) -> tuple[SegmentationResult, pathlib.Path]:
        request = SegmentationRequest(
            image_path=pathlib.Path(image_path),
            prompt=prompt,
            out_dir=out_dir,
            max_candidates=max_candidates,
        )
        request_path = out_dir / REQUEST_FILENAME
        request_path.write_text(
            json.dumps(request.to_dict(), ensure_ascii=False), encoding="utf-8"
        )

        started = time.perf_counter()
        completed = self._launch(request_path)
        wall = time.perf_counter() - started

        if completed.returncode != 0:
            raise SegmentationError(
                "SAM 3 の実行に失敗しました"
                f"（終了コード {completed.returncode}）: {_tail(completed.stderr)}"
            )

        result = _read_result(out_dir / RESULT_FILENAME)
        return (_with_wall_time(result, wall), out_dir)

    def _launch(self, request_path: pathlib.Path) -> subprocess.CompletedProcess:
        """子プロセスを起動して待つ。

        **`PYTHONPATH` にリポジトリの根を足す。** 専用環境にはこのアプリが
        入っていないので、こうしないと子プロセスが契約
        （`contracts.py`）を読めない。契約を2か所に書き写すよりは、
        パスを1つ渡すほうが食い違いようがない。
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        # 子プロセスの標準出力・エラーを UTF-8 で受ける（Windows の既定は CP932）
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            return subprocess.run(
                [str(self.python_path), str(self.cli_path), str(request_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise SegmentationError(
                f"SAM 3 の実行が {self.timeout} 秒で終わりませんでした"
            ) from e
        except OSError as e:
            raise SegmentationError(f"SAM 3 を起動できませんでした: {e}") from e

    def discard(self, out_dir: pathlib.Path) -> None:
        """候補の置き場ごと片付ける。**作品には何も残さない。**

        「中止」（結果を使わない → 計画 段階5）と、適用が終わったあとの
        後始末の両方がここを通る。消せなくても黙って進む——一時フォルダが
        1つ残るだけで、利用者にできることが無い。
        """
        shutil.rmtree(out_dir, ignore_errors=True)


def _tail(text: str, limit: int = 400) -> str:
    """エラー文の末尾だけ。**全部出すと、画面が読めなくなる。**"""
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _read_result(path: pathlib.Path) -> SegmentationResult:
    if not path.is_file():
        raise SegmentationError("SAM 3 が結果を書きませんでした")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SegmentationError(f"SAM 3 の結果が読めませんでした: {e}") from e
    return SegmentationResult.from_dict(data)


def _with_wall_time(result: SegmentationResult, wall: float) -> SegmentationResult:
    """時間の内訳に、依頼した側から見た実測を入れる。

    **`startup` は引き算で出す。** 子プロセスが自分で測れるのは、自分が
    動き出したあとだけ。利用者が待つのは起動そのものを含めた時間なので、
    「全体 − 子が測った時間」を起動待ちとして持つ（→ `contracts.Timings`）。
    """
    inner = result.timings
    return SegmentationResult(
        image_px=result.image_px,
        candidates=result.candidates,
        prompt=result.prompt,
        model=result.model,
        timings=Timings(
            startup=max(0.0, wall - inner.total),
            load=inner.load,
            infer=inner.infer,
            total=wall,
        ),
        gpu_peak_mb=result.gpu_peak_mb,
    )
