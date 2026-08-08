"""点検（書き出す前の見回り）— 要件定義 10.1

**直さない。見つけて返すだけ。** 直し方（縮める／書き足す／消す）は場面ごとに
違うので、判断は利用者に残す。「収まるように縮める」を入れると、枠に収まらない
字を隠さずに出す（→ 6.5）・小さい用紙でも詰め直さない（→ 6.1）と食い違う。

**画面のことを何も知らない。** ここは `Project` を読んで `Finding` を並べるだけで、
窓も付箋も出てこない。画像が使えるかどうかだけは外から渡してもらう
（`has_asset`）——実体の在り処を知っているのは画面の側だけで、ここへ持ち込むと
純粋な計算でなくなる。**同じ関数を書き出し前の警告
（`ui.export.missing_assets_in`）も使う**ので、2つの機能が同じ画像に違う答えを
返すことがない（2026-08-09 に集約）。

**用紙からのはみ出しは拾わない**（2026-08-06 本人確認済み）。紙の端まで絵を
出すコマ（断ち切り）は普通の使い方なので、拾うと窓が常に埋まり、埋まった窓は
読まれなくなる。`layout.outside_page` 自体は残す——あちらは**ページの大きさを
変えた直後**に「今この操作で外へ出た」ことを知らせるためのもので（→ 6.1）、
用途が違う。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .layout import balloon_contains, page_assets, text_ink_bands
from .model import BalloonObject, Page, Project, TextObject
from .vertical import COLUMN_PITCH

# 拾うものの種類（→ 要件定義 6.29）
KIND_MISSING_ASSET = "missing_asset"
KIND_BLANK_PAGE = "blank_page"
KIND_TEXT_OVERFLOW = "text_overflow"
KIND_EMPTY_TEXT = "empty_text"
KIND_EMPTY_PANEL = "empty_panel"
KIND_NOTE_LEFT = "note_left"

KIND_LABELS = {
    # 「見つからない」と言い切らないのは、**実体はあるが画像として開けない**
    # 場合も同じ扱いだから（→ `EditorState.has_asset`。2026-08-09）。
    # 書き出した結果はどちらも「そこが白く抜ける」で同じ
    KIND_MISSING_ASSET: "使えない画像",
    KIND_BLANK_PAGE: "何も置いていないページ",
    KIND_TEXT_OVERFLOW: "フキダシからはみ出したセリフ",
    KIND_EMPTY_TEXT: "空のままのセリフ",
    KIND_EMPTY_PANEL: "絵の入っていないコマ",
    KIND_NOTE_LEFT: "付箋の残っているページ",
}

# 重い順。**この順で窓に出す**（→ 要件定義 6.29「重さの違い」）
KIND_ORDER = (
    KIND_MISSING_ASSET,
    KIND_BLANK_PAGE,
    KIND_TEXT_OVERFLOW,
    KIND_EMPTY_TEXT,
    KIND_EMPTY_PANEL,
    KIND_NOTE_LEFT,
)

# 見出し。**「実体の無い画像」と「付箋が残っている」を同じ並びに置かない。**
# 前者は書き出した絵が白く抜け、後者は自分で貼った目印にすぎない
GROUP_FIX = "直し忘れ"
GROUP_LEFTOVER = "消し忘れ"
GROUP_ORDER = (GROUP_FIX, GROUP_LEFTOVER)
KIND_GROUPS = {kind: GROUP_FIX for kind in KIND_ORDER} | {KIND_NOTE_LEFT: GROUP_LEFTOVER}

# セリフのはみ出しを見逃す幅（字の大きさに対する割合）。
#
# **厳しくしすぎない**（→ 要件定義 6.29）。縦書きの帯は列の送りぶんの幅を持ち、
# 楕円のフキダシでは字の角が少し外へ出るのが普通の使い方。ここを 0 にすると
# 一覧が常に埋まり、埋まった一覧は読まれなくなる。
OVERFLOW_SLACK = 0.25


@dataclass(frozen=True)
class Finding:
    """見つかったもの1件。

    **`page_id` を持つ**。番号だけだと、点検したあとにページを並べ替えた
    とき、印が別のページに付いたまま残る。
    """

    page_index: int
    page_id: str
    kind: str
    object_id: str | None = None

    @property
    def page_number(self) -> int:
        """人が読む番号（1 始まり）。"""
        return self.page_index + 1


def inspect_project(
    project: Project, has_asset: Callable[[str], bool] | None = None
) -> list[Finding]:
    """作品を丸ごと見回す。**重い順 → ページ順**に並べて返す。

    `has_asset` は「その参照の画像が使えるか」（実体があり、画像として
    開ける形か → `EditorState.has_asset`）。**省くと画像の欠けは見ない。**
    確かめられるのは画面の側だけなので、渡されなければその項目を
    飛ばす（数だけ勝手に 0 と答えると、無事だったのと区別が付かない）。
    """
    found: list[Finding] = []
    for index, page in enumerate(project.pages):
        found.extend(inspect_page(page, index, has_asset))
    found.sort(key=lambda f: (KIND_ORDER.index(f.kind), f.page_index))
    return found


def inspect_page(
    page: Page, page_index: int, has_asset: Callable[[str], bool] | None = None
) -> list[Finding]:
    """1ページぶん。並びは呼び出し側で整える。"""

    def hit(kind: str, object_id: str | None = None) -> Finding:
        return Finding(page_index, page.id, kind, object_id)

    found: list[Finding] = []

    # **何も置いていないページ**（2026-08-06 に追加）。書き出すと白紙が1枚出る。
    #
    # 空のコマ1つは拾うのに白紙は素通り、では判定が逆立ちしている。
    # **ラフ（→ 6.23）は数えない。** 書き出しでは切られる（`PageRenderer`）ので、
    # 敷いてあっても書き出した結果は白紙のまま
    if not page.panels and not page.floating:
        found.append(hit(KIND_BLANK_PAGE))

    if has_asset is not None:
        # **コマの中の画像とマークの両方を見る**（→ `layout.page_assets`）
        for obj in page_assets(page):
            if not obj.asset or not has_asset(obj.asset):
                found.append(hit(KIND_MISSING_ASSET, obj.id))

    balloons = {f.id: f for f in page.floating if isinstance(f, BalloonObject)}
    for obj in page.floating:
        if not isinstance(obj, TextObject):
            continue
        if not obj.content.strip():
            found.append(hit(KIND_EMPTY_TEXT, obj.id))
            continue  # 空のセリフのはみ出しは見ない（枠そのものが帯になる）
        balloon = balloons.get(obj.attached_balloon_id or "")
        # **フキダシに紐づいていないセリフは対象外。** コマの外へ直接置く
        # ナレーションは正常な使い方であって、はみ出しではない（→ 6.5）
        if balloon is not None and text_overflows(obj, balloon):
            found.append(hit(KIND_TEXT_OVERFLOW, obj.id))

    for panel in page.panels:
        # 集中線・流線だけのコマは空ではない。絵は入っていなくても描くものがある
        if panel.children or panel.focus_lines is not None or panel.flow_lines is not None:
            continue
        found.append(hit(KIND_EMPTY_PANEL, panel.id))

    if page.note is not None:
        found.append(hit(KIND_NOTE_LEFT))

    return found


def text_overflows(text: TextObject, balloon: BalloonObject) -> bool:
    """セリフの字が、フキダシの輪郭から外へ出ているか。

    **判定は掴み所と同じ2つの部品でできている**（→ 要件定義 10.1）。字の並ぶ
    範囲は `layout.text_ink_bands`、フキダシの内側は `layout.balloon_contains`。
    幾何計算をここで新しく起こさない——起こすと、見た目・掴み所・点検の3つが
    別々にずれていく。

    **フォントを測らないので、自動テストで確かめられる。** 検証環境には
    フォントが1つも無い（→ 6.5・6.11）が、`text_ink_bands` は字送りの計算
    だけで帯を出しており、実際の字形は見ていない。
    """
    return any(
        not balloon_contains(balloon, x, y) for x, y in _overflow_points(text)
    )


def _overflow_points(text: TextObject) -> list[tuple[float, float]]:
    """はみ出しを確かめる点。帯の四隅を、見逃す幅ぶん内へ寄せたもの。"""
    size = text.font.size_px
    if not text.content.strip() or size <= 0.0:
        return []

    slack = size * OVERFLOW_SLACK
    if text.direction == "vertical":
        # 帯の幅は列の送り（字の 1.33 倍）。**字そのものより広い**ので、
        # まず送りの余りを削り、そのうえで見逃す幅ぶん内へ寄せる
        inset_x = size * (COLUMN_PITCH - 1.0) / 2.0 + slack
    else:
        # 横書きは Qt が行を組むので**字送りが分からず、帯の幅は枠のまま**
        # （→ `layout.text_ink_bands`）。左右で判定すると、字が短いだけの
        # セリフまで全部はみ出しになる。中心の縦だけを見る
        inset_x = None

    points: list[tuple[float, float]] = []
    for band in text_ink_bands(text):
        left, right = _shrink(band.x, band.right, inset_x)
        top, bottom = _shrink(band.y, band.bottom, slack)
        points += [(left, top), (right, top), (left, bottom), (right, bottom)]
    return points


def _shrink(low: float, high: float, inset: float | None) -> tuple[float, float]:
    """両端を内へ寄せる。詰まりすぎたら真ん中に潰す（`inset` が None でも同じ）。"""
    if inset is None or high - low <= inset * 2.0:
        middle = (low + high) / 2.0
        return middle, middle
    return low + inset, high - inset


# -- 読むための文（→ 要件定義 10.1「結果は2か所に出る」） ----------------------


def marked_page_ids(findings: list[Finding]) -> set[str]:
    """紫の印を付けるページ。**種類で分けない**（印は1色 → 要件定義 10.1）。"""
    return {f.page_id for f in findings}


def headline(findings: list[Finding]) -> str:
    """状態表示に出す1行。"""
    if not findings:
        return "点検しました。直し忘れは見つかりませんでした"
    pages = len(marked_page_ids(findings))
    return f"{pages} ページで {len(findings)} 件 見つかりました"


def summary_lines(findings: list[Finding]) -> list[str]:
    """窓に出す文。**重い順に、種類ごとまとめて**並べる。

    ページ番号を並べるところまでで止める。「どのセリフか」まで書いても、
    番号を読んで自分で探すことになって使われない。**どのページかが分かれば、
    紫の印が付いた一覧から押して開ける**（→ 要件定義 10.1）。
    """
    if not findings:
        return ["直し忘れは見つかりませんでした。"]

    lines: list[str] = []
    for group in GROUP_ORDER:
        kinds = [
            kind
            for kind in KIND_ORDER
            if KIND_GROUPS[kind] == group and any(f.kind == kind for f in findings)
        ]
        if not kinds:
            continue
        if lines:
            lines.append("")
        lines.append(f"■ {group}")
        for kind in kinds:
            hits = [f for f in findings if f.kind == kind]
            lines.append(f"　{KIND_LABELS[kind]}（{len(hits)}件）")
            lines.append(f"　　　{_page_list(hits)}")
    return lines


def _page_list(findings: list[Finding]) -> str:
    """「3ページ、7ページ ×2」。同じページに複数あれば数を添える。"""
    counts = Counter(f.page_number for f in findings)
    parts = [
        f"{number}ページ" + (f" ×{count}" if count > 1 else "")
        for number, count in sorted(counts.items())
    ]
    return "、".join(parts)
