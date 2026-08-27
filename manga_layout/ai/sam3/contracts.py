"""AI 提供者に渡す入力と、返ってくる結果の形（→ `SAM3実装計画.md` 4章）。

**ここに PyTorch も SAM 3 も出てこない。** アプリ側が知るのはこのファイルの
型だけで、実際に動かす経路（推論ごとに起動する CLI か、常駐ワーカーか）も、
モデルが何かも外側に置く。提供者を差し替えるときに、画面・保存・描画を
一緒に作り替えないための線がここ。

やり取りは**入力JSON・出力JSON・候補マスクPNG の3つだけ**にする。JSON へ
書くマスクの在り処は出力フォルダからの相対名にとどめ、元画像の絶対パスは
結果側に入れない（→ 4.5 実験ログの取り決めと同じ理由）。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...errors import MangaLayoutError


class SegmentationError(MangaLayoutError):
    """切り抜きの依頼が果たせなかった。

    専用環境が無い、起動に失敗した、返ってきた JSON が読めない、のいずれか。
    **候補が0件なのは失敗ではない**（結果として空の一覧が返る）。利用者に
    伝えることが違う——前者は「使える状態になっていない」、後者は
    「見つかりませんでした」。
    """


def _mapping(data, where: str) -> dict:
    if not isinstance(data, dict):
        raise SegmentationError(f"{where}: 対応表（辞書）ではありません")
    return data


def _number(d: dict, key: str, where: str) -> float:
    value = d.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SegmentationError(f"{where}.{key}: 数値ではありません（{value!r}）")
    return float(value)


def _text(d: dict, key: str, where: str, default: str | None = None) -> str:
    value = d.get(key, default)
    if not isinstance(value, str):
        raise SegmentationError(f"{where}.{key}: 文字列ではありません（{value!r}）")
    return value


def _box(data, where: str) -> tuple[int, int, int, int]:
    """元画像のピクセル座標での範囲 (x, y, 幅, 高さ)。"""
    if not isinstance(data, (list, tuple)) or len(data) != 4:
        raise SegmentationError(f"{where}: 4つの数の並びではありません（{data!r}）")
    values = []
    for i, item in enumerate(data):
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise SegmentationError(f"{where}[{i}]: 数値ではありません（{item!r}）")
        values.append(int(item))
    if values[2] < 0 or values[3] < 0:
        raise SegmentationError(f"{where}: 幅と高さが負です（{values}）")
    return (values[0], values[1], values[2], values[3])


@dataclass(frozen=True)
class SegmentationRequest:
    """1回の推論に渡すもの。

    `out_dir` は候補マスクPNGと出力JSONの置き場。**作品フォルダではない**
    ——適用するまで作品を一切変えない取り決めなので（→ 計画 5章 段階5）、
    候補は捨ててよい場所に書く。
    """

    image_path: pathlib.Path
    prompt: str
    out_dir: pathlib.Path
    # 返してほしい候補の数の上限。多すぎると選ぶ側が見比べられない
    max_candidates: int = 8

    def to_dict(self) -> dict[str, Any]:
        # **パスは `as_posix()` で書く。** Windows の区切りをそのまま
        # JSON へ入れると、読み書きのどこかで区切り文字の解釈が要る
        return {
            "image": self.image_path.as_posix(),
            "prompt": self.prompt,
            "out_dir": self.out_dir.as_posix(),
            "max_candidates": self.max_candidates,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "request") -> SegmentationRequest:
        d = _mapping(data, where)
        return cls(
            image_path=pathlib.Path(_text(d, "image", where)),
            prompt=_text(d, "prompt", where),
            out_dir=pathlib.Path(_text(d, "out_dir", where)),
            max_candidates=int(_number(d, "max_candidates", where)),
        )


@dataclass(frozen=True)
class MaskCandidate:
    """候補1つ。**マスクの実体はPNGファイルで、JSONには名前だけ入れる。**

    画素をJSONへ入れない理由は、4K のマスクが文字にすると10MB規模になるから。
    `name` は出力フォルダからの相対名で、実際の在り処の組み立ては受け取った
    側が行う（`path_in`）。
    """

    index: int
    name: str
    # モデルが返す確からしさ（0〜1）。**絵の良し悪しではない**ので、
    # 並べ替えの目安に使うだけで、これで自動採用はしない
    score: float
    # 元画像のピクセル座標での範囲 (x, y, 幅, 高さ)
    box: tuple[int, int, int, int]

    def path_in(self, out_dir: pathlib.Path) -> pathlib.Path:
        return out_dir / self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "score": self.score,
            "box": list(self.box),
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> MaskCandidate:
        d = _mapping(data, where)
        name = _text(d, "name", where)
        # **出力フォルダの外を指す名前は受け取らない。** 区切り文字の判定は
        # `PureWindowsPath` に任せる（Windows は / も区切りとして扱うので、
        # 両方の書き方をこれ1つで弾ける）
        if name in ("", ".", "..") or pathlib.PureWindowsPath(name).name != name:
            raise SegmentationError(f"{where}.name: ファイル名として使えません（{name!r}）")
        return cls(
            index=int(_number(d, "index", where)),
            name=name,
            score=_number(d, "score", where),
            box=_box(d.get("box"), f"{where}.box"),
        )


@dataclass(frozen=True)
class Timings:
    """1回の推論の時間の内訳（秒）。**足し算が合うとは限らない。**

    `startup` は依頼した側から見た起動待ちで、残りは子プロセスが自分で測る。
    `total` は依頼した側の実測——利用者が待つのはこれで、内訳では説明の
    付かない差もここに出る（→ 計画 4.5・段階4）。
    """

    startup: float = 0.0
    load: float = 0.0
    infer: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "startup": self.startup,
            "load": self.load,
            "infer": self.infer,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Timings:
        if data is None:
            return cls()
        d = _mapping(data, where)
        return cls(
            startup=_number(d, "startup", where) if "startup" in d else 0.0,
            load=_number(d, "load", where) if "load" in d else 0.0,
            infer=_number(d, "infer", where) if "infer" in d else 0.0,
            total=_number(d, "total", where) if "total" in d else 0.0,
        )


@dataclass(frozen=True)
class SegmentationResult:
    """1回の推論の結果。

    **候補が0件でも結果である。** 「見つかりませんでした」と伝えて元画像を
    一切変えないのが正しい終わり方なので、例外にはしない（→ 計画 3章）。
    """

    image_px: tuple[int, int]
    candidates: tuple[MaskCandidate, ...] = ()
    prompt: str = ""
    # 実際に動いたモデルのリビジョン。実験ログの突き合わせに使う
    model: str = ""
    timings: Timings = field(default_factory=Timings)
    # GPU メモリの最大値（MB）。測れなかった環境では None
    gpu_peak_mb: float | None = None

    @property
    def found(self) -> bool:
        return bool(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_px": [self.image_px[0], self.image_px[1]],
            "candidates": [c.to_dict() for c in self.candidates],
            "prompt": self.prompt,
            "model": self.model,
            "timings": self.timings.to_dict(),
            "gpu_peak_mb": self.gpu_peak_mb,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "result") -> SegmentationResult:
        d = _mapping(data, where)
        px = d.get("image_px")
        if not isinstance(px, (list, tuple)) or len(px) != 2:
            raise SegmentationError(f"{where}.image_px: 2つの数の並びではありません（{px!r}）")
        # 数かどうかの判定を `_box` と共有する（幅・高さの位置に 0 を置いて渡す）
        width, height, _w, _h = _box([px[0], px[1], 0, 0], f"{where}.image_px")
        if width <= 0 or height <= 0:
            raise SegmentationError(f"{where}.image_px: 画素数が0以下です（{px!r}）")

        raw = d.get("candidates", [])
        if not isinstance(raw, list):
            raise SegmentationError(f"{where}.candidates: 並びではありません（{raw!r}）")

        peak = d.get("gpu_peak_mb")
        if peak is not None and (
            not isinstance(peak, (int, float)) or isinstance(peak, bool)
        ):
            raise SegmentationError(f"{where}.gpu_peak_mb: 数値ではありません（{peak!r}）")

        return cls(
            image_px=(width, height),
            candidates=tuple(
                MaskCandidate.from_dict(item, f"{where}.candidates[{i}]")
                for i, item in enumerate(raw)
            ),
            prompt=_text(d, "prompt", where, ""),
            model=_text(d, "model", where, ""),
            timings=Timings.from_dict(d.get("timings"), f"{where}.timings"),
            gpu_peak_mb=None if peak is None else float(peak),
        )


class SegmentationProvider(Protocol):
    """切り抜きを頼める相手。**SAM 3 はこれの実装の1つでしかない。**

    テストは偽の提供者を渡して境界だけを確かめる（モデルも GPU も要らない）。
    """

    def segment(self, request: SegmentationRequest) -> SegmentationResult: ...
