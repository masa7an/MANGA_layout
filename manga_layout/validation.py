"""project.json を読むときの型チェック。

JSON は何でも入るので、モデルへ変換する前にここで弾く。
黙って既定値へ差し替えると、利用者のデータが静かに書き換わってしまうため、
**壊れていたら必ず例外を投げる**方針にしている。

どの例外にも `where`（例: `pages[0].panels[2].border`）を含める。
これが無いと、数百個のオブジェクトを持つファイルで原因箇所を探せない。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .errors import ProjectFormatError

# #RRGGBB または #RRGGBBAA
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

_MISSING = object()


def _fail(where: str, message: str) -> None:
    raise ProjectFormatError(f"{where}: {message}")


def req_mapping(value: Any, where: str) -> Mapping[str, Any]:
    """辞書であることを確かめる。"""
    if not isinstance(value, Mapping):
        _fail(where, f"辞書ではありません（{type(value).__name__}）")
    return value


def req_list(value: Any, where: str) -> Sequence[Any]:
    """配列であることを確かめる。文字列は配列として扱わない。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(where, f"配列ではありません（{type(value).__name__}）")
    return value


def _pick(data: Mapping[str, Any], key: str, where: str, default: Any) -> Any:
    if key in data:
        return data[key]
    if default is _MISSING:
        _fail(where, f"必須の項目 '{key}' がありません")
    return default


def number(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> float:
    """数値を取り出す。

    bool は int の派生なので Python では数値として通ってしまう。
    `visible: true` を線幅として読み込む事故を防ぐため明示的に弾く。
    """
    value = _pick(data, key, where, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{where}.{key}", f"数値ではありません（{value!r}）")
    return float(value)


def positive(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> float:
    """0 より大きい数値を取り出す。ページ寸法など、0 だと破綻する項目に使う。"""
    value = number(data, key, where, default)
    if value <= 0:
        _fail(f"{where}.{key}", f"0 より大きい値が必要です（{value}）")
    return value


def integer(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> int:
    value = _pick(data, key, where, default)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{where}.{key}", f"整数ではありません（{value!r}）")
    return value


def text(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> str:
    value = _pick(data, key, where, default)
    if not isinstance(value, str):
        _fail(f"{where}.{key}", f"文字列ではありません（{value!r}）")
    return value


def flag(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> bool:
    value = _pick(data, key, where, default)
    if not isinstance(value, bool):
        _fail(f"{where}.{key}", f"true / false ではありません（{value!r}）")
    return value


def choice(
    data: Mapping[str, Any],
    key: str,
    where: str,
    allowed: Iterable[str],
    default: Any = _MISSING,
) -> str:
    """決められた語のいずれかであることを確かめる。

    知らない値を黙って既定値に倒すと、利用者から見て設定が勝手に変わる。
    """
    value = text(data, key, where, default)
    allowed = tuple(allowed)
    if value not in allowed:
        _fail(f"{where}.{key}", f"{' / '.join(allowed)} のいずれかである必要があります（{value!r}）")
    return value


def color(data: Mapping[str, Any], key: str, where: str, default: Any = _MISSING) -> str:
    """#RRGGBB か #RRGGBBAA の色指定を取り出す。"""
    value = text(data, key, where, default)
    if not _COLOR_RE.match(value):
        _fail(f"{where}.{key}", f"#RRGGBB 形式の色ではありません（{value!r}）")
    return value


def opt_text(data: Mapping[str, Any], key: str, where: str) -> str | None:
    """文字列または null を取り出す。attached_panel_id のように「無い」が正常な項目に使う。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(f"{where}.{key}", f"文字列でも null でもありません（{value!r}）")
    return value


def opt_ratio(data: Mapping[str, Any], key: str, where: str) -> float | None:
    """-1.0〜1.0 の数値、または null を取り出す。

    しっぽの付け根の高さのように「指定なし＝自動」が正常な項目に使う。
    範囲外は切り詰めずに弾く。黙って直すと、書き出した値と読み戻した値が
    食い違い、保存のたびに形が変わる。
    """
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{where}.{key}", f"数値でも null でもありません（{value!r}）")
    if not -1.0 <= value <= 1.0:
        _fail(f"{where}.{key}", f"-1.0〜1.0 の範囲外です（{value}）")
    return float(value)


def point(value: Any, where: str) -> tuple[float, float]:
    """[x, y] の 2 要素を取り出す。"""
    seq = req_list(value, where)
    if len(seq) != 2:
        _fail(where, f"[x, y] の 2 要素が必要です（{len(seq)} 要素）")
    out = []
    for i, v in enumerate(seq):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            _fail(f"{where}[{i}]", f"数値ではありません（{v!r}）")
        out.append(float(v))
    return (out[0], out[1])
