"""書き出したファイルを、別名で書き切ってから置き換える。

PNG/JPG（`ui.export.write_image`）と PSD（`psd.write_psd`）の両方が使う。
**中身の書き方（Qt の `QImage.save` かバイト列か）はここでは決めない**——
そこは形式ごとに違うので、呼ぶ側が済ませてから渡す。ここが持つのは
「書けた `tmp` を本来の名前へ入れ替える」という、両方で共通する最後の
1歩だけ（2026-08-08、重複していた置き換え処理をここへ集約）。
"""

from __future__ import annotations

import os
import pathlib

from .errors import ExportError

TMP_SUFFIX = ".tmp"


def tmp_path_for(path: pathlib.Path) -> pathlib.Path:
    """書き切るための仮の置き場所。"""
    return path.with_name(path.name + TMP_SUFFIX)


def replace_or_raise(tmp: pathlib.Path, path: pathlib.Path) -> None:
    """`tmp` を `path` の位置へ不可分に入れ替える。

    失敗すれば `tmp` を消して `ExportError` にする。**`tmp` を残さない。**
    残っていると、次に同じ場所へ書き出したときに前回の失敗作と紛れる。
    """
    try:
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ExportError(
            f"{path.name} を置き換えられませんでした（{e}）。"
            "他のアプリで開いたままになっていないか確かめてください"
        ) from e
