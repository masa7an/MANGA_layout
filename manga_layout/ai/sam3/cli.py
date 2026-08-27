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


@dataclass(frozen=True)
class Outcome:
    """1回の推論で分かったこと。

    **寸法を候補と別に持つ。** 候補が0件でも元画像の寸法は分かるので、
    「見つかりませんでした」を寸法つきで返せる（0×0 だと受け取る側が読めない）。
    """

    image_px: tuple[int, int]
    predictions: list[Prediction]
    # モデルを読むのに掛かった秒。2回目以降（控えが効いたとき）は 0
    load_seconds: float = 0.0


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


# 公式の配布元（→ 段階0 で確かめた `sam3.model_builder.download_ckpt_from_hf`）。
# **自分で取りに行く。** `build_sam3_image_model(load_from_HF=True)` に任せると、
# どのリビジョンを読んだかが手元に残らない（実験ログに書けない → 4.5）
HF_REPO = "facebook/sam3"
HF_CKPT = "sam3.pt"

# これ未満の確からしさの候補は返らない（`Sam3Processor` の既定と同じ）。
# **下げると候補は増えるが、選ぶ側が見比べきれなくなる。** 段階6で動かす
CONFIDENCE_THRESHOLD = 0.5

# 同じプロセスで2回目以降、モデルを読み直さないための控え。
# **今は推論ごとにプロセスを起こすので出番が無い**（→ 計画 段階4）。
# 常駐ワーカーへ替えたときに、ここがそのまま効く
_PROCESSOR = None
_REVISION = ""


def model_revision() -> str:
    """実際に動いたモデルのリビジョン。**読む前は空。**

    Hugging Face のキャッシュは `snapshots/<リビジョン>/<ファイル>` の形に
    なっているので、取ってきたパスから拾える。
    """
    return _REVISION


def _load(device: str):
    """モデルを読む。2回目からは控えを返す。戻り値は (処理役, 掛かった秒)。"""
    global _PROCESSOR, _REVISION
    if _PROCESSOR is not None:
        return _PROCESSOR, 0.0

    # **時計は import より先に読む。** `import sam3` だけで 4.5 秒掛かり
    # （2026-08-27 実測）、あとで測ると**その4.5秒が「推論」に混ざる**。
    # 実際そう書いていて、初通しで推論 5.4 秒という値を出した
    started = time.perf_counter()

    import torch  # noqa: F401
    from huggingface_hub import hf_hub_download
    from sam3 import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    checkpoint = pathlib.Path(hf_hub_download(repo_id=HF_REPO, filename=HF_CKPT))
    # .../snapshots/<リビジョン>/sam3.pt
    _REVISION = checkpoint.parent.name if checkpoint.parent.parent.name == "snapshots" else ""
    model = build_sam3_image_model(
        device=device, load_from_HF=False, checkpoint_path=str(checkpoint)
    )
    _PROCESSOR = Sam3Processor(
        model, device=device, confidence_threshold=CONFIDENCE_THRESHOLD
    )
    return _PROCESSOR, time.perf_counter() - started


def _predict(request: SegmentationRequest) -> Outcome:
    """公式 SAM 3 に切り抜きを頼む。**ここだけが PyTorch と SAM 3 に触る。**

    呼び方は 2026-08-27 に実機で確かめたもの（根拠: 単体推論を実行し、
    2048×2048 の生成画像に "person" で候補1件・信頼度 0.863 を得た）。

    **`torch.autocast` で包むのは呼ぶ側の仕事。** 動画側の経路
    （`sam3_base_predictor.py`）は自分で包んでいるが、画像側の
    `Sam3Processor` は包んでいない。包まないと vitdet の中で
    「BFloat16 と Float がぶつかる」で落ちる（同日実測）。

    返すのは**元画像の寸法と候補**。寸法を候補から取らないのは、候補が0件の
    ときに寸法が分からなくなるため。
    """
    import torch
    from PIL import Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor, load_seconds = _load(device)

    image = Image.open(request.image_path).convert("RGB")
    width, height = image.size

    with torch.autocast(device_type=device, dtype=torch.bfloat16):
        state = processor.set_image(image)
        state = processor.set_text_prompt(request.prompt, state)

    masks, boxes, scores = state["masks"], state["boxes"], state["scores"]
    # **確からしさの高い順に並べ替える。** モデルが返す順は決まっていない
    order = sorted(range(len(scores)), key=lambda i: -float(scores[i]))

    predictions = []
    for i in order:
        # マスクは [候補, 1, 高さ, 幅] の真偽値で、**元画像と同じ寸法**
        # （`Sam3Processor._forward_grounding` が元寸へ戻している）
        mask = masks[i][0].to(torch.uint8) * 255
        predictions.append(
            Prediction(
                mask=mask.contiguous().cpu().numpy().tobytes(),
                width=width,
                height=height,
                score=float(scores[i]),
                box=_box_of(boxes[i], width, height),
            )
        )
    return Outcome(
        image_px=(width, height), predictions=predictions, load_seconds=load_seconds
    )


def _box_of(box, width: int, height: int) -> tuple[int, int, int, int]:
    """モデルの [x0, y0, x1, y1] を、契約の (x, y, 幅, 高さ) に直す。

    **画像の外へはみ出した値は切り詰める。** はみ出したまま渡すと、範囲を
    見て「どのくらい選ばれたか」を測る側が、画像より大きい面積を出す。
    """
    x0, y0, x1, y1 = (float(v) for v in box)
    x0 = max(0, min(width, round(x0)))
    y0 = max(0, min(height, round(y0)))
    x1 = max(0, min(width, round(x1)))
    y1 = max(0, min(height, round(y1)))
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def run(request: SegmentationRequest) -> SegmentationResult:
    """依頼を1件こなす。**候補が0件でも、結果として返す。**"""
    started = time.perf_counter()
    outcome = _predict(request)
    infer_done = time.perf_counter()
    image_px, predictions = outcome.image_px, outcome.predictions

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
            load=outcome.load_seconds,
            # モデルを読む時間を引いた、絵に対する推論そのもの
            infer=max(0.0, (infer_done - started) - outcome.load_seconds),
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
