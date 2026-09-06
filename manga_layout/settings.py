"""アプリの設定（`settings.json`）。

**作品ではなく、この PC でのこのアプリの好み**を入れる。作品に属するもの
（ページの大きさ、綴じ方向）は `project.json` 側で、ここには入れない。
混ぜると、作品を別の PC へ渡したときに相手の好みを上書きしてしまう。

置き場所は**このリポジトリの `data/settings.json`**。`data/` は git 管理外
なので（`.gitignore` の `/data/`）、`F:` のような**片方の PC にしか無い
ドライブ**が書かれていても、もう1台へは同期されない。2台運用で困らない
という条件は、`%LOCALAPPDATA%` に置かなくても満たせる。

`%LOCALAPPDATA%` から移した理由は、**そこに置くと「誰が見ているファイル
なのか」が分からなくなった**ため。パッケージ版アプリの中から触ると
`%LOCALAPPDATA%` は `...\\Packages\\<アプリ>\\LocalCache\\Local\\` へ
転送されるが、**パス表示は元のまま変わらない**。同じパスなのに実体が別で、
片方では設定が入っていて片方では空、という状態になる（2026-08-03 に
実際に起きて、原因の特定に長くかかった）。作業フォルダの中なら実体が1つ
しかなく、この取り違えが起こらない。

**代わりに失うもの:** clone し直すと設定も消える。手で書き直す前提の
数行なので、取り違えの分かりにくさとは釣り合わないと判断した。

人が手で開いて書き換えることを前提にした形にしてある（項目を絞る、
知らない項目は捨てずに読み飛ばす、壊れていても起動を止めない）。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from .model import DEFAULT_FONT_FAMILY
from .storage import atomic_write_text

# 利用者のデータを入れるフォルダ。git 管理外（`.gitignore` の `/data/`）
DATA_DIRNAME = "data"
SETTINGS_FILENAME = "settings.json"

# 形式を変えたときに、古い設定を読んでいると気づけるようにする
SETTINGS_VERSION = 1

# 自動バックアップの間隔（秒 → 要件定義 6.6）。
#
# **設定に出しているのは、動きを確かめる手段でもあるため。** 既定の5分は
# 待って確かめるには長く、かといってコードを 30 秒に書き換えて確かめると
# 戻し忘れがそのまま出荷される。ここに出しておけば、確かめたい間だけ
# 短くして、あとで消せる。
AUTOSAVE_INTERVAL_DEFAULT_SEC = 300

# 受け付ける範囲。下限は「確かめるための最短」、上限は「これ以上空けるなら
# 切ったのと変わらない」という目安。外れた値は既定値に落とす（設定は
# 手で書き換える前提なので、打ち間違いで起動を止めない）
AUTOSAVE_INTERVAL_MIN_SEC = 5
AUTOSAVE_INTERVAL_MAX_SEC = 3600

# JPG 書き出しの品質（要件定義 6.7）。Qt の `QImage.save` にそのまま渡す値
# （0〜100、大きいほど高画質・大容量）。**書き出しダイアログには出さない。**
# 書き出し dpi（→ 要件定義 10.2）と同じ位置づけで、必要な人が設定ファイルを
# 手で書き換える前提にする。ダイアログに出すと選択肢がもう1つ増える
JPG_QUALITY_DEFAULT = 90

# 受け付ける範囲。0 は「ほぼ潰れた画像」になり書き出す意味が無いので下限を 1 に、
# 上限は Qt の仕様上の最大値
JPG_QUALITY_MIN = 1
JPG_QUALITY_MAX = 100

# ラフ（下敷き → 要件定義 6.23）の濃さ。0.0 で透明、1.0 でそのまま。
#
# **ここに出しているのは、作品ではなく紙の見え方の好みだから。** 濃いラフは
# なぞる線がよく見える代わりにコマ枠と紛れ、薄いラフはその逆になる。どちらが
# 良いかは元のラフの濃さ（鉛筆かペンか、写真かスキャンか）で変わるので、
# 決め打ちにできない。既定の 40% は薄い鉛筆書きを写真で撮った場合に合わせてある
ROUGH_OPACITY_DEFAULT = 0.4

# 受け付ける範囲。0 は「敷いていないのと同じ」で、敷いたのに何も出ない状態を
# 設定の打ち間違いで作れてしまうため下限を設ける
ROUGH_OPACITY_MIN = 0.05
ROUGH_OPACITY_MAX = 1.0


def settings_dir() -> pathlib.Path:
    """設定を置くフォルダ。**このリポジトリの `data/`。**

    起動時の作業フォルダではなく、**このファイルの位置から**辿る。
    どこから起動しても同じ1個のファイルを指すようにするため
    （`run.bat` から、tools/ のスクリプトから、と入口が複数ある）。
    """
    # settings.py → manga_layout/ → リポジトリのルート
    return pathlib.Path(__file__).resolve().parent.parent / DATA_DIRNAME


def settings_path() -> pathlib.Path:
    return settings_dir() / SETTINGS_FILENAME


def _autosave_interval(value: object) -> int:
    """設定から読んだ間隔を秒で返す。**受け付けられない値は既定に落とす。**

    真偽値を弾いているのは、Python では `True` が `1` として通ってしまい、
    `"autosave_interval_sec": true` と書かれたときに 1 秒間隔になるため。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return AUTOSAVE_INTERVAL_DEFAULT_SEC
    seconds = int(value)
    if not AUTOSAVE_INTERVAL_MIN_SEC <= seconds <= AUTOSAVE_INTERVAL_MAX_SEC:
        return AUTOSAVE_INTERVAL_DEFAULT_SEC
    return seconds


def _jpg_quality(value: object) -> int:
    """設定から読んだ JPG 品質を返す。**受け付けられない値は既定に落とす。**"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return JPG_QUALITY_DEFAULT
    quality = int(value)
    if not JPG_QUALITY_MIN <= quality <= JPG_QUALITY_MAX:
        return JPG_QUALITY_DEFAULT
    return quality


def _rough_opacity(value: object) -> float:
    """設定から読んだラフの濃さを返す。**受け付けられない値は既定に落とす。**"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ROUGH_OPACITY_DEFAULT
    opacity = float(value)
    if not ROUGH_OPACITY_MIN <= opacity <= ROUGH_OPACITY_MAX:
        return ROUGH_OPACITY_DEFAULT
    return opacity


# よく使う書体を覚えておける数（→ 要件定義 6.5）。
#
# **3つで足りる。** 実際に使うのは3〜4種類で、書体を選ぶ窓には 199 件が
# 並ぶ（このPCでの実測 → `ui/font_dialog`）。増やすほど押す前に迷うので、
# 「見ずに押せる数」で切る。
FAVORITE_FONT_SLOTS = 3

# 1枠目の既定。**空で始めない。**
#
# 3枠とも空だと、道具箱にボタンが1つも出ず、**機能があること自体が
# 画面に出ない。** 既定の書体を1つ入れておけば、ボタンが1つ見えて、
# 「ここに並ぶものは設定で増やせる」と分かる
DEFAULT_FAVORITE_FONTS = [DEFAULT_FONT_FAMILY, "", ""]


def _favorite_fonts(value: object) -> list[str]:
    """設定から読んだ「よく使う書体」を返す。**受け付けられない値は捨てる。**

    書体名は文字列でしか書けないので、数や真偽値が混ざっていたらその枠だけ
    空にする。**枠ごと詰めない**——2番目を消したときに3番目が繰り上がると、
    覚えた並び順が黙って変わる。

    **多い分は切り、足りない分は空で埋める。** 常に `FAVORITE_FONT_SLOTS`
    個の並びとして扱えるので、読む側が長さを確かめずに済む。

    **入っている書体かどうかはここでは見ない。** 設定を読むのはアプリの
    どの層でも起こりうるが、書体の一覧を引けるのは画面のある所だけ
    （`QFontDatabase`）。ここで見に行くと、設定の読み込みが Qt に依存する
    （→ 「中核は Qt を知らない」ではないが、settings.py は今のところ知らない）
    """
    if not isinstance(value, list):
        return list(DEFAULT_FAVORITE_FONTS)
    names = [item if isinstance(item, str) else "" for item in value]
    names = names[:FAVORITE_FONT_SLOTS]
    return names + [""] * (FAVORITE_FONT_SLOTS - len(names))


@dataclass
class AppSettings:
    """`settings.json` の中身。

    `default_parent_dir` は**ファイルの窓が始まる場所**。作品フォルダ
    そのものではなく、その1つ上を指す。`null` にするとドキュメント
    フォルダを使う。

    「名前を付けて保存」「作品を開く」「画像を選ぶ」で**共通**に使う。
    窓ごとに分けると、同じ作業の途中なのに始まる場所が変わり、
    そのたびに辿り直すことになる。

    `autosave_interval_sec` は**自動バックアップの間隔**（→ 6.6）。
    短くすれば動きを確かめられる。範囲外・数でない値は既定の5分に落とす。

    `jpg_quality` は**JPG 書き出しの品質**（→ 6.7）。0〜100 で既定は 90。
    ダイアログには出さないので、変えたい人はここを手で書き換える。

    `rough_opacity` は**ラフ（下敷き）の濃さ**（→ 6.23）。0.05〜1.0 で
    既定は 0.4。元のラフの濃さに合わせて手で書き換える。

    `favorite_fonts` は**よく使う書体**（→ 6.5）。3枠あり、`F3` と道具箱の
    ボタンから押して切り替える。空の枠は「未登録」で、押す先にも出ない。
    """

    default_parent_dir: str | None = None
    autosave_interval_sec: int = AUTOSAVE_INTERVAL_DEFAULT_SEC
    jpg_quality: int = JPG_QUALITY_DEFAULT
    rough_opacity: float = ROUGH_OPACITY_DEFAULT
    favorite_fonts: list[str] = field(
        default_factory=lambda: list(DEFAULT_FAVORITE_FONTS)
    )

    @property
    def registered_fonts(self) -> list[str]:
        """登録されている書体だけを、枠の並び順で返す。

        **空の枠は落とす。** 押す先（キーとボタン）はどちらもここを見るので、
        「未登録の枠を押したら何も起きない」という当たりが生まれない。
        """
        return [name for name in self.favorite_fonts if name]

    def to_dict(self) -> dict:
        return {
            "format_version": SETTINGS_VERSION,
            "default_parent_dir": self.default_parent_dir,
            "autosave_interval_sec": self.autosave_interval_sec,
            "jpg_quality": self.jpg_quality,
            "rough_opacity": self.rough_opacity,
            "favorite_fonts": list(self.favorite_fonts),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AppSettings:
        """辞書から作る。**知らない項目は黙って読み飛ばす。**

        設定は人が手で書き換えるものなので、打ち間違いや古い項目が
        混ざる。1個の綴り間違いで起動しなくなるほうが困る。
        """
        value = data.get("default_parent_dir")
        return cls(
            default_parent_dir=value if isinstance(value, str) and value else None,
            autosave_interval_sec=_autosave_interval(data.get("autosave_interval_sec")),
            jpg_quality=_jpg_quality(data.get("jpg_quality")),
            rough_opacity=_rough_opacity(data.get("rough_opacity")),
            favorite_fonts=_favorite_fonts(data.get("favorite_fonts")),
        )


def load_settings(path: pathlib.Path | None = None) -> AppSettings:
    """設定を読む。**無い・壊れているときは既定値を返す。**

    起動を止めない。設定はあくまで好みで、無くても作業はできる。
    """
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    return AppSettings.from_dict(data)


def save_settings(settings: AppSettings, path: pathlib.Path | None = None) -> pathlib.Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)
    return path


def load_raw_settings(path: pathlib.Path | None = None) -> dict | None:
    """設定ファイルの中身を**そのまま**返す。読めなければ `None`。

    `load_settings` との違いは、**知らない項目も、範囲外の値も、書いてある
    まま返す**こと。書き戻すときに消さないため（→ `update_settings_file`）と、
    「書いたのに採用されなかった値」を道具側で見つけるために要る。

    `None` は「ファイルが無い」と「壊れている」の両方を指す。区別が要る側は
    ファイルの有無で分ける（設定を調整する道具は、壊れている場合にだけ
    「保存すると置き換わる」と知らせる → 要件定義 6.31）。
    """
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def update_settings_file(
    settings: AppSettings, path: pathlib.Path | None = None
) -> pathlib.Path:
    """**知っている項目だけを差し替えて**書き戻す。

    `save_settings` との違いは、**知らない項目を消さない**こと。設定は手で
    書き換える前提のファイルなので、まだアプリが知らない項目や、将来のために
    書き足した項目が混ざりうる。道具から保存するたびにそれが黙って消えると、
    「触っていない所が無くなった」に気づけない。

    アプリ本体は今までどおり `save_settings`（雛形を置くときだけ使う）。
    こちらは設定を調整する道具から使う（→ 要件定義 6.31）。
    """
    data = load_raw_settings(path) or {}
    data.update(settings.to_dict())
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)
    return path


def ensure_settings_file(path: pathlib.Path | None = None) -> pathlib.Path:
    """無ければ既定値で作る。**あるものには触らない。**

    手で書き換える前提のファイルなので、実物が無いと「どこに何を書けば
    いいのか」が分からない。起動時に一度呼んで、空の雛形を置いておく。

    **書き込みに失敗しても起動は止めない**（ディスク容量不足・権限エラー
    など → 2026-08-08 発見）。雛形が置けないだけで、`load_settings` 側は
    無ければ既定値を返すので動作に支障は無い。
    """
    path = path or settings_path()
    if not path.is_file():
        try:
            save_settings(load_settings(path), path)
        except OSError:
            pass
    return path
