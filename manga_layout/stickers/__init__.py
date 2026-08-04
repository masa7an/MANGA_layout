"""アプリに組み込んだマークの素材（要件定義 6.14）。

**PNG の実体をこのフォルダに置き、コードと一緒に管理する。** `data/` は
git の管理外なので、そちらに置くと2台のPCで揃わないうえ、消えても
気づけない。

素材の取り込みは `tools/import_sticker.py` が行う（透明な余白を削って
から入れる）。ここは**読むだけ**。

**貼った実体はここから引き継がれない。** 置いた時点で `assets/` へ
SHA1 で入るので（→ 5章）、作品はこのフォルダに依存しない。あとで素材を
差し替えても、既にある作品の見た目は変わらない。
"""

from __future__ import annotations

import pathlib

from ..errors import AssetError

STICKER_EXCLAIM = "exclaim"
STICKER_EXCLAIM_QUESTION = "exclaim_question"

# 組み込んである種類。**保存形式の `kind` に書かれる値そのもの**なので、
# 増やすのはよいが既存の値を変えてはいけない（既にある作品が指している）。
#
# 画面の呼び名はここに持たない。呼び名は画面の都合で変わるものなので、
# `ui.state.STICKER_KIND_LABELS` に置いて保存形式と切り離してある
# （フキダシの `BALLOON_STYLE_LABELS` と同じ形 → 6.4）
STICKER_KINDS = (STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION)

_DIR = pathlib.Path(__file__).parent


def sticker_path(kind: str) -> pathlib.Path:
    return _DIR / f"{kind}.png"


def read_sticker(kind: str) -> bytes:
    """組み込み素材の中身。無ければ `AssetError`。

    **知らない種類は読まずに断る。** ここで拾わないと、利用者には
    「マークを置いたのに何も出ない」としか見えない。
    """
    if kind not in STICKER_KINDS:
        known = " / ".join(STICKER_KINDS)
        raise AssetError(f"知らないマークの種類です: {kind!r}（あるのは {known}）")
    path = sticker_path(kind)
    try:
        return path.read_bytes()
    except OSError as e:
        raise AssetError(f"マークの素材を読めませんでした: {path}（{e}）") from e
