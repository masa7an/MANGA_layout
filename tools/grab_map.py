"""掴みの地図。**押した場所ごとに「何が掴めるか」を塗り分けた1枚**を作る。

`PageView.mousePressEvent` は17か所で打ち切る順序依存のチェーンで、並び順
そのものが仕様になっている。**順序を変える改修が、掴めるものの分布を
変えていないか**を目で見て確かめるための道具。

`tests/test_press_reach.py`（層1）が「つまみの中心で狙ったものが掴めるか」を
点で見るのに対し、こちらは**面で見る。** 中心では勝っていても、掴める範囲が
痩せていれば人には「掴みにくくなった」と感じられる——それは点では出ない。

**pytest には入れていない。** 全部の場面を舐めると分単位になり、通し
（20秒台）の性質が変わってしまう。**改修の前後に手で回して、出てきた
`digest.txt` を突き合わせる**使い方をする。

使い方（venv の python で）::

    ./venv/Scripts/python.exe tools/grab_map.py                    # 全場面
    ./venv/Scripts/python.exe tools/grab_map.py --scenes フキダシ,集中線
    ./venv/Scripts/python.exe tools/grab_map.py --step 1 --scale 1.0 --scale 0.25
    ./venv/Scripts/python.exe tools/grab_map.py --out data/掴みの地図_あと \
        --check data/掴みの地図/digest.txt

**突き合わせるときは `--out` で別のフォルダを指す。** 書き出しは突き合わせより
先に走るので、**ゴールデンと同じ場所へ書くと、上書きした自分自身と比べることに
なり、必ず「差分なし」が出る**（そして元のゴールデンは消えている）。
同じ場所を指したときは走る前に止める。

出来るもの（既定で `data/掴みの地図/`。**`data/` は git 管理外**）::

    <場面>@<倍率>.png   塗り分けた地図。1画素＝ページの 1px
    index.html          全部を並べた1枚（色の見出し付き）
    digest.txt          場面ごとの内訳と sha256。**突き合わせるのはこれ**
                        1行目に刻み・場面数・**窓の大きさ**が入る（窓が違うと
                        境目の点がずれるので、比べてよい相手かがそこで分かる）

**刻みは既定 2px。** いちばん小さいつまみは画面上 9px（`HANDLE_PX`）なので、
それより粗いと**つまみを丸ごと飛ばす**（8px 刻みで、しっぽの先端に1点しか
当たらなかった実測がある → 2026-09-06 の記録）。

日本語を画面に出すので、Windows のコンソールでは `PYTHONUTF8=1` を付ける。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# 画面を出さずに動かす。QApplication を作る前に決めておく必要がある
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from manga_layout import Rect  # noqa: E402
from manga_layout.model import BalloonObject  # noqa: E402
from manga_layout.ui import EditorState, MainWindow  # noqa: E402
from manga_layout.ui.canvas import SPLIT_TOOLS  # noqa: E402
from manga_layout.ui.state import TOOL_SELECT, TOOL_WAND  # noqa: E402

OUT_DIR = ROOT / "data" / "掴みの地図"

# つまみの周りをどれだけ余分に見るか（ページの px）。
# 縮小すると掴む範囲はページの上で広がるので、その分の余地
MARGIN = 140.0

# 場面に何も置かれていないときに舐める範囲（ページの中央）
EMPTY_ROI = 600.0

# 塗り分けの色。**名前で固定する。** 出てきた順に振ると、
# 種類が1つ増えただけで全部の色が入れ替わり、前後の絵が比べられなくなる
COLORS: dict[str, str] = {
    "なし": "#EEEEEE",
    "MoveDrag": "#BFD3F2",
    "ResizeDrag": "#3B6CB7",
    # セリフの四隅。**大きさ変更（濃い青）と近い色にする。** 隣り合った
    # つまみで別のドラッグになるので、地図の上では「同じ仲間の別の面」
    # として見えるほうが読み取りやすい
    "TextScaleDrag": "#6BA3E8",
    "RotateDrag": "#7B68EE",
    "TailDrag": "#E8743B",
    "TailRootDrag": "#F2C14E",
    "SlantDrag": "#4E9A57",
    "FocusDrag": "#C0504D",
    "FlowDrag": "#9B59B6",
    "CreatePanelDrag": "#2E4053",
    "CreateFloatingDrag": "#17A589",
    "RoughMoveDrag": "#A9CCE3",
    "RoughResizeDrag": "#22618F",
    "ToneAreaDrag": "#D98880",
}
UNKNOWN = "#FF00FF"  # 表に無い種類。**目立つ色にして気づけるようにする**


def scenes() -> dict[str, object]:
    """場面は `tests/test_press_reach.py` の `場面_` から拾う。

    **場面を2か所に書かない。** 層1のテストと同じ場面を見ていないと、
    地図が変わったときに「作りが変わったのか、場面が違うのか」が
    分からなくなる。あちらに場面を足せば、こちらにも自動で増える。
    """
    import test_press_reach as reach

    return {
        name.removeprefix("場面_"): fn
        for name, fn in vars(reach).items()
        if name.startswith("場面_")
    }


def build(setup) -> MainWindow:
    window = MainWindow(EditorState())
    window.state.set_tool(TOOL_SELECT)
    setup(window)
    return window


def roi_of(window) -> Rect:
    """舐める範囲。置いてあるもの全部を囲って、少し広げる。"""
    page = window.state.page
    rects: list[Rect] = [panel.bounds() for panel in page.panels]
    rects += [child.rect for panel in page.panels for child in panel.children]
    rects += [obj.rect for obj in page.floating]
    if page.rough is not None:
        rects.append(page.rough.rect)
    bounds = window.state.selected_bounds
    if bounds is not None:
        rects.append(bounds)

    # しっぽの先端は本体の外に出る。囲いから外すと先端の丸が地図に写らない
    tips = [
        obj.tail.tip
        for obj in page.floating
        if isinstance(obj, BalloonObject) and obj.tail.enabled
    ]

    if not rects and not tips:
        cx, cy = page.size.w / 2.0, page.size.h / 2.0
        half = EMPTY_ROI / 2.0
        return Rect(cx - half, cy - half, EMPTY_ROI, EMPTY_ROI)

    xs = [r.x for r in rects] + [t[0] for t in tips]
    ys = [r.y for r in rects] + [t[1] for t in tips]
    right = [r.right for r in rects] + [t[0] for t in tips]
    bottom = [r.bottom for r in rects] + [t[1] for t in tips]
    x, y = min(xs) - MARGIN, min(ys) - MARGIN
    return Rect(x, y, max(right) + MARGIN - x, max(bottom) + MARGIN - y)


def label_at(view, x: float, y: float) -> str:
    """そこを押したときの答え。**ドラッグの種類と、選ばれたもの。**

    層1（`test_press_reach.py`）は種類だけを見ているが、地図には**選んだ
    ものの id も入れる。** 種類だけだと、コマを掴んで動かすのとフキダシを
    掴んで動かすのが同じ `MoveDrag` になり、**選び直しの相手が変わった改修**
    が地図に写らない。押下の最後の一手は「その場所にあるものを選ぶ」
    （→ `_pick_at`）なので、そこが変わったことは見えないと困る。

    **色は種類だけで塗る**（→ `paint`）。id まで色を分けると、場面ごとに
    色が変わって前後の絵が比べられなくなる。id は `digest.txt` の側で効く。
    """
    from mouse import press

    view._drag = None
    press(view, x, y)
    drag = view._drag
    name = type(drag).__name__ if drag is not None else "なし"
    for attr in ("handle", "kind"):
        if getattr(drag, attr, None) is not None:
            name += f"[{getattr(drag, attr)}]"
    view._drag = None
    return f"{name}({view.state.selected_id or '選択なし'})"


def sweep(window, roi: Rect, step: int) -> list[list[str]]:
    """範囲を格子状に押して、名前の表を作る。

    **押すたびに選択と道具を元へ戻す。** 戻さないと、空白を押した1点が
    選択を変え、そこから先の地図が別の場面のものになる。
    """
    state = window.state
    view = window.view
    if state.tool in SPLIT_TOOLS or state.tool == TOOL_WAND:
        raise SystemExit(
            f"この場面は押すたびに作品を書き換える道具（{state.tool}）を持っている。"
            "地図は取れない"
        )

    selected, tool = state.selected_id, state.tool
    depth = state.history.depth
    rows = []
    for y in range(int(roi.y), int(roi.bottom), step):
        row = []
        for x in range(int(roi.x), int(roi.right), step):
            row.append(label_at(view, float(x), float(y)))
            if state.selected_id != selected:
                state.select(selected)
            if state.tool != tool:
                state.set_tool(tool)
        rows.append(row)

    if state.history.depth != depth:
        raise SystemExit(
            "地図を取る間に作品が変わった（履歴が積まれた）。押した先で"
            "書き換えが起きているので、この場面は地図に向かない"
        )
    return rows


def paint(rows: list[list[str]], step: int) -> QImage:
    """名前の表を1枚の絵にする。**1画素＝ページの 1px。**"""
    height = len(rows) * step
    width = len(rows[0]) * step if rows else 0
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for row_index, row in enumerate(rows):
        for col_index, name in enumerate(row):
            kind = name.split("[")[0].split("(")[0]
            color = QColor(COLORS.get(kind, UNKNOWN)).rgb()
            for dy in range(step):
                for dx in range(step):
                    image.setPixel(col_index * step + dx, row_index * step + dy, color)
    return image


def digest_of(name: str, scale: float, roi: Rect, step: int, rows) -> str:
    """突き合わせるための1場面ぶんの記録。**内訳と、表そのもののハッシュ。**

    内訳だけだと「同じ数だけ入れ替わった」を見逃す。ハッシュだけだと
    どこが変わったのか読めない。両方を並べて置く。
    """
    counts = Counter(name for row in rows for name in row)
    raw = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    lines = [
        f"## {name} @{scale:g}倍  範囲=({roi.x:g},{roi.y:g})-({roi.right:g},{roi.bottom:g})"
        f"  刻み={step}px  点={sum(counts.values())}"
    ]
    lines += [f"  {label:<40} {count}" for label, count in sorted(counts.items())]
    lines.append(f"  sha256 = {hashlib.sha256(raw).hexdigest()}")
    return "\n".join(lines)


def write_html(out: Path, made: list[tuple[str, float, str]]) -> Path:
    """全部を並べた1枚。**色の見出しはここにしか出せない。**

    画面なしで動かしているので、この環境には使えるフォントが1つも無い
    （`QFontDatabase.families()` が空 → `tests/test_ui_text.py`）。
    絵の中に文字を描いても出ないので、見出しは HTML の側に置く。
    """
    swatches = "".join(
        f'<li><i style="background:{color}"></i>{label}</li>'
        for label, color in COLORS.items()
    )
    figures = "".join(
        f"<figure><figcaption>{name} @{scale:g}倍</figcaption>"
        f'<img src="{file}" alt="{name}"></figure>'
        for name, scale, file in made
    )
    path = out / "index.html"
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>掴みの地図</title>"
        "<style>body{font-family:sans-serif;font-size:20px;background:#fff;color:#222}"
        "ul{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:12px}"
        "li{display:flex;align-items:center;gap:6px}"
        "i{width:20px;height:20px;display:inline-block;border:1px solid #999}"
        "figure{margin:0 0 24px}figcaption{margin-bottom:4px;font-weight:bold}"
        "img{border:1px solid #999;image-rendering:pixelated}</style>"
        "<h1>掴みの地図</h1>"
        f"<ul>{swatches}</ul>{figures}",
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="掴みの地図を作る")
    parser.add_argument("--scenes", help="場面を絞る（カンマ区切り）")
    parser.add_argument(
        "--step", type=int, default=2, help="刻み（ページの px。既定 2）"
    )
    parser.add_argument(
        "--scale",
        type=float,
        action="append",
        help="表示倍率（複数指定できる。既定 1.0）",
    )
    parser.add_argument("--out", default=str(OUT_DIR), help="書き出し先")
    parser.add_argument("--check", help="前に取った digest.txt と突き合わせる")
    args = parser.parse_args(argv)

    scales = args.scale or [1.0]
    out = Path(args.out)
    # **走る前に止める。** 突き合わせは書き出しのあとに読むので、同じ場所を
    # 指していると「上書きした自分自身」と比べて必ず差分なしになる。
    # 8分かけたうえで嘘の合格が出るのが、この道具のいちばん危ない壊れ方
    if args.check and Path(args.check).resolve() == (out / "digest.txt").resolve():
        raise SystemExit(
            f"突き合わせ先が書き出し先と同じ: {args.check}\n"
            f"  先に上書きしてから読むので、必ず「差分なし」になる。\n"
            f"  --out で別のフォルダを指す（例: --out {out}_あと）"
        )
    out.mkdir(parents=True, exist_ok=True)

    QApplication.instance() or QApplication([])
    available = scenes()
    wanted = args.scenes.split(",") if args.scenes else list(available)
    unknown = [name for name in wanted if name not in available]
    if unknown:
        raise SystemExit(f"知らない場面: {unknown} ／ あるのは {list(available)}")

    parts = [f"# 掴みの地図 digest ／ 刻み={args.step}px ／ 場面={len(wanted)}"]
    made: list[tuple[str, float, str]] = []
    # 押した点は画面の整数 px へ丸めてから場面の座標へ戻る（→ `tests/mouse.py`）
    # ので、**窓の大きさが違うと境目の点がずれうる。** 見出しに入れておけば、
    # 別の大きさで取ったものと比べたときに、差分の1行目がそれを教える
    viewport = "?"
    for name in wanted:
        for scale in scales:
            started = time.perf_counter()
            window = build(available[name])
            view = window.view
            viewport = f"{view.viewport().width()}x{view.viewport().height()}"
            view.zoom_by(scale / view.view_scale, at_mouse=False)
            roi = roi_of(window)
            rows = sweep(window, roi, args.step)

            file = f"{name}@{scale:g}.png"
            paint(rows, args.step).save(str(out / file), "PNG")
            parts.append(digest_of(name, view.view_scale, roi, args.step, rows))
            made.append((name, scale, file))

            window.state.history.mark_saved()
            window.close()
            points = len(rows) * len(rows[0])
            # **その都度流す。** 1場面に1分前後かかるので、ファイルへ
            # 逃がして眺めているときに溜め込まれると進み具合が分からない
            print(
                f"{name} @{scale:g}倍  {points} 点  {time.perf_counter() - started:.1f}秒"
                f"  -> {file}",
                flush=True,
            )

    parts[0] += f" ／ 窓={viewport}"
    digest = "\n".join(parts) + "\n"
    (out / "digest.txt").write_text(digest, encoding="utf-8")
    page = write_html(out, made)
    print(f"書き出した: {out / 'digest.txt'} ／ {page}")

    if args.check:
        import difflib

        before = Path(args.check).read_text(encoding="utf-8").splitlines()
        diff = list(
            difflib.unified_diff(
                before, digest.splitlines(), args.check, "今回", lineterm=""
            )
        )
        if not diff:
            print("突き合わせ: 差分なし")
            return 0
        print("突き合わせ: 差分あり")
        print("\n".join(diff))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
