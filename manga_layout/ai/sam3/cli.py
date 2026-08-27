"""専用環境の中でだけ動く入口（→ `SAM3実装計画.md` 4.1・段階4）。

**公式 SAM 3 と PyTorch を import してよいのは、このファイルだけ。**
アプリの `venv` からは実行しない。呼ぶのは `runner.py` で、専用環境の python に
このファイルを渡す形になる。

    <専用環境のpython> cli.py <request.json>

書き出すのは**候補ごとの8bitグレースケールPNGと、出力JSON1つ**（→ 4章）。
JSON と PNG の読み書きは `contracts.py` と共有し、**画素の書き出しは標準ライブラリ
だけで行う**（zlib と struct）。専用環境に画像ライブラリを増やさずに済む
——増やすほど、SAM 3 本体との版の衝突が起きやすくなる（→ 段階0 の numpy の件）。

**モデルを呼ぶ所（`_predict`）は、まだ空けてある。** 公式の呼び方は、専用環境を
作って実際に動かしてから書く（段階4）。当てずっぽうで書くと、動かないコードが
「実装済み」に見える。
"""

from __future__ import annotations

import json
import pathlib
import struct
import sys
import time
import zlib
from dataclasses import dataclass

# **時計はいちばん先に読む。** ここより前は python 自身の起動で、子からは測れない
_START = time.perf_counter()

from manga_layout.ai.sam3.contracts import (  # noqa: E402
    MaskCandidate,
    SegmentationRequest,
    SegmentationResult,
    Timings,
)

RESULT_FILENAME = "result.json"


@dataclass(frozen=True)
class Prediction:
    """モデルが返した候補1つを、画素のバイト列にまで落としたもの。

    **ここから先に numpy も torch も出てこない。** 配列から `bytes` への
    変換までを `_predict` の内側で済ませておけば、書き出しの側はモデルの
    都合を知らずに済む（試験も、モデル無しで通せる）。

    `mask` は1画素1バイト（0 か 255）、左上から右下へ幅×高さぶん。
    """

    mask: bytes
    width: int
    height: int
    score: float
    box: tuple[int, int, int, int]


def write_gray_png(path: pathlib.Path, width: int, height: int, mask: bytes) -> None:
    """8bitグレースケールPNGを1枚書く。**標準ライブラリだけで書く。**

    フィルタは使わない（各行の先頭に 0 を置くだけ）。マスクは同じ値が続く
    絵なので、フィルタを掛けなくても zlib がよく縮む。

    書き方は `tests/fixtures/make_fixtures.py` と同じ。あちらは試験用の画像を
    作るためのもので、こちらは専用環境で動く側——**共有はしない**
    （専用環境からアプリのテストを読ませない）。
    """
    expected = width * height
    if len(mask) != expected:
        raise ValueError(f"画素数が合いません（期待 {expected} / 実際 {len(mask)}）")

    raw = b"".join(
        bytes([0]) + mask[y * width : (y + 1) * width] for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(
        bytes([137]) + b"PNG" + bytes([13, 10, 26, 10])
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def gpu_peak_mb() -> float | None:
    """GPU メモリの最大値（MB）。測れなければ None。

    **測れないことと 0 を分ける。** CPU で動かした場合や、torch が入って
    いない場合に 0 と答えると、「使っていない」のか「測っていない」のかが
    実験ログから区別できなくなる（→ 4.5）。
    """
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return torch.cuda.max_memory_allocated() / (1024 * 1024)


def model_revision() -> str:
    """実際に動いたモデルのリビジョン。**分からなければ空。**

    段階4で、公式の読み込み方が決まってから埋める（→ `_predict`）。
    """
    return ""


def _predict(
    request: SegmentationRequest,
) -> tuple[tuple[int, int], list[Prediction]]:
    """公式 SAM 3 に切り抜きを頼む。**ここだけが段階4の仕事。**

    返すのは**元画像の寸法と、候補の一覧**。寸法を候補から取らないのは、
    **候補が0件のときに寸法が分からなくなる**ため（結果が「0×0の絵」に
    なって、受け取る側が読めない。試験で見つけた）。絵を読むのはここなので、
    ここが答えるのが素直。

    埋めるときに確かめること。

    1. 公式の読み込み方（`from sam3...` の形と、チェックポイントの指定）
    2. 文（`request.prompt`）の渡し方と、返る候補の形
    3. 候補ごとのマスクを 0/255 の1画素1バイトへ落とす手順（numpy はここで使う）
    4. 信頼度と範囲（元画像のピクセル座標）の取り出し方

    **当てずっぽうで書かない。** 動かないコードが「実装済み」に見えるのが、
    この計画でいちばん避けたい状態（→ 段階0「動くと書くのは通ってから」）。
    """
    raise NotImplementedError(
        "SAM 3 の呼び出しはまだ書いていません（段階4）。"
        "専用環境を作り、公式の呼び方を確かめてからここを埋めてください"
    )


def run(request: SegmentationRequest) -> SegmentationResult:
    """依頼を1件こなす。**候補が0件でも、結果として返す。**"""
    load_started = time.perf_counter()
    image_px, predictions = _predict(request)
    infer_done = time.perf_counter()

    request.out_dir.mkdir(parents=True, exist_ok=True)
    candidates = []
    for i, prediction in enumerate(predictions[: request.max_candidates]):
        if (prediction.width, prediction.height) != image_px:
            # **元画像と同じ寸法でないマスクは書かない**（→ 4.2）。ここで
            # 通すと、受け取った側が適用のときに断ることになり、利用者は
            # 候補を選んだあとで断られる
            raise ValueError(
                f"候補 {i} の寸法が元画像と違います"
                f"（{prediction.width}×{prediction.height} / 元画像 {image_px[0]}×{image_px[1]}）"
            )
        name = f"cand{i}.png"
        write_gray_png(
            request.out_dir / name, prediction.width, prediction.height, prediction.mask
        )
        candidates.append(
            MaskCandidate(
                index=i, name=name, score=prediction.score, box=prediction.box
            )
        )

    return SegmentationResult(
        image_px=image_px,
        candidates=tuple(candidates),
        prompt=request.prompt,
        model=model_revision(),
        timings=Timings(
            # **`load` と `infer` は段階4で分ける。** 今はモデルを呼ぶ所が
            # 1つの関数なので、まとめて `infer` に入れておく
            load=0.0,
            infer=infer_done - load_started,
            total=time.perf_counter() - _START,
        ),
        gpu_peak_mb=gpu_peak_mb(),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("使い方: cli.py <request.json>", file=sys.stderr)
        return 2

    request = SegmentationRequest.from_dict(
        json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    )
    result = run(request)
    # **時間は書き出す直前に取り直す。** 書き出しも利用者の待ち時間の一部
    result = SegmentationResult(
        image_px=result.image_px,
        candidates=result.candidates,
        prompt=result.prompt,
        model=result.model,
        timings=Timings(
            startup=result.timings.startup,
            load=result.timings.load,
            infer=result.timings.infer,
            total=time.perf_counter() - _START,
        ),
        gpu_peak_mb=result.gpu_peak_mb,
    )
    (request.out_dir / RESULT_FILENAME).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
