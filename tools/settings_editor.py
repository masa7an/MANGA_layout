"""設定（`data/settings.json`）を窓から書き換える道具（→ 要件定義 6.28）。

設定は**手で書き換える前提**のファイルだが、手書きだと次の5つで詰まる。

- フォルダの場所は `\\` を2つ重ねて書く決まり（JSON の書き方）で、間違えると
  ファイルごと読めなくなる
- 範囲外・書き間違いは**黙って既定値に戻る**。起動を止めない作りの裏返しで、
  「書き換えたのに効かない」理由が画面に出ない
- どの項目に何を書けるかを README で確かめる必要がある
- 効くタイミングが項目ごとに違う（間隔だけはアプリを開き直す）
- 実在しないフォルダ（外付けが繋がっていない）を書いても分からない

この道具はその5つだけを引き受ける。**設定の項目そのものは増やさない**し、
手で書き換える道もそのまま残る（この道具は必須ではない）。

使い方
------
    settings.bat をダブルクリック

    ./venv/Scripts/python.exe tools/settings_editor.py
"""

from __future__ import annotations

import pathlib
import sys

# tools/ から実行しても manga_layout を見つけられるようにする
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from manga_layout.settings import (  # noqa: E402
    AUTOSAVE_INTERVAL_DEFAULT_SEC,
    AUTOSAVE_INTERVAL_MAX_SEC,
    AUTOSAVE_INTERVAL_MIN_SEC,
    JPG_QUALITY_DEFAULT,
    JPG_QUALITY_MAX,
    JPG_QUALITY_MIN,
    ROUGH_OPACITY_DEFAULT,
    ROUGH_OPACITY_MAX,
    ROUGH_OPACITY_MIN,
    AppSettings,
    load_raw_settings,
    load_settings,
    settings_path,
    update_settings_file,
)

# 設定の項目名 → 画面での呼び名。「書いたのに採用されなかった」を
# 知らせるときに、設定ファイルの綴りではなく画面の言葉で出すため
FIELD_LABELS = {
    "default_parent_dir": "最初に開くフォルダ",
    "autosave_interval_sec": "自動バックアップの間隔",
    "jpg_quality": "JPG の品質",
    "rough_opacity": "ラフの濃さ",
}

# ラフの濃さの刻み。0.05 は「1段変えて違いが分かる」下限として選んだ
ROUGH_OPACITY_STEP = 0.05


def normalized_dir(text: str) -> str | None:
    """入力されたフォルダを、設定に書く形へ整える。空なら `None`。

    整えるのは2つだけ。

    - **区切りを揃える。** ファイル窓は `F:/2025_e/...` と `/` で返すが、
      設定ファイルを人が読むとき Windows の見慣れた `\\` と混ざると、
      書き方が2通りあるように見える
    - **前後の `"` を落とす。** エクスプローラーの「パスのコピー」は
      `"F:\\..."` と引用符ごと渡してくる

    `\\` を2つ重ねる JSON の書き方は `json` 側が受け持つので、ここでは
    何もしない（人が意識しなくてよくなるのが、この道具の目的の1つ）。
    """
    text = text.strip().strip('"')
    if not text:
        return None
    return str(pathlib.Path(text))


def dropped_fields(raw: dict | None, settings: AppSettings) -> list[str]:
    """**書いてあるのに採用されなかった**項目の呼び名。

    範囲外・型違いは既定値に落ちる（`load_settings`）。落ちたこと自体は
    正しい動きだが、手で書き換えた本人からは「効かない」としか見えない。
    ここで拾って画面に出す。

    「書いていない」と「書いたが落ちた」は区別する。書いていない項目は
    既定で動くのが当たり前で、知らせても雑音にしかならない。
    """
    if not raw:
        return []
    adopted = settings.to_dict()
    names = []
    for key, label in FIELD_LABELS.items():
        if key not in raw:
            continue
        written = raw[key]
        # 「指定なし」を意図して書いた `null` / `""` は打ち間違いではない
        if key == "default_parent_dir" and written in (None, ""):
            continue
        # `True` は Python では `1` として通ってしまうので、値が一致していても
        # 採用されていない（設定側で弾いている）
        if isinstance(written, bool) or written != adopted[key]:
            names.append(label)
    return names


class SettingsEditor(QDialog):
    """設定の4項目を並べて書き換える窓。

    **範囲は入力欄が守る。** 入れられない値をそもそも入力できないので、
    「保存したのに既定に戻っている」が起きない。

    保存は「保存して閉じる」だけ、取り消しの確認は出さない。設定は数行で、
    間違えても開き直して入れ直せる（コマ削除に確認を付けていないのと
    同じ判断 → 要件定義 6.2）。
    """

    def __init__(self, path: pathlib.Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("設定の調整")
        self.path = path or settings_path()

        raw = load_raw_settings(self.path)
        settings = load_settings(self.path)
        # ファイルはあるのに読めない＝書き方が壊れている。「まだ無い」とは分ける
        self.broken = self.path.is_file() and raw is None

        self.top_note = self._top_note(raw, settings)

        layout = QVBoxLayout(self)
        layout.addWidget(self.top_note)
        layout.addLayout(self._form(settings))
        layout.addWidget(QLabel(f"設定ファイル: {self.path}", self))
        layout.addWidget(self._buttons())

        self._update_folder_note()

    # -- 画面の組み立て ------------------------------------------------------

    def _top_note(self, raw: dict | None, settings: AppSettings) -> QLabel:
        """読めなかったときだけ、置き換わることを先に知らせる。

        壊れた設定を黙って上書きすると、**手で書いた中身が消えたことに
        気づけない**。ここで一度出しておけば、消したくない人は保存せずに
        閉じて、元のファイルを直せる。
        """
        note = QLabel(self)
        note.setWordWrap(True)
        if self.broken:
            note.setText(
                "今の設定ファイルは読めませんでした（書き方が壊れています）。"
                "既定の値を出しています。保存すると今の中身は置き換わります。"
            )
        else:
            dropped = dropped_fields(raw, settings)
            if dropped:
                note.setText(
                    "次の項目は、書かれている値を使えなかったので既定に戻して"
                    "います: " + "、".join(dropped)
                )
            else:
                note.setVisible(False)
        return note

    def _form(self, settings: AppSettings) -> QFormLayout:
        self.folder = QLineEdit(settings.default_parent_dir or "", self)
        self.folder.setPlaceholderText("空にすると ドキュメント から始まります")
        self.folder.textChanged.connect(self._update_folder_note)

        choose = QPushButton("選ぶ…", self)
        choose.clicked.connect(self._choose_folder)
        clear = QPushButton("空にする", self)
        clear.clicked.connect(self.folder.clear)

        picker = QHBoxLayout()
        picker.addWidget(self.folder)
        picker.addWidget(choose)
        picker.addWidget(clear)

        # 実在しないときだけ出す。**保存は止めない**（外付けドライブが
        # 繋がっていない日にも、先に設定しておけたほうがよい）
        self.folder_note = QLabel(self)
        self.folder_note.setWordWrap(True)

        self.interval = QSpinBox(self)
        self.interval.setRange(AUTOSAVE_INTERVAL_MIN_SEC, AUTOSAVE_INTERVAL_MAX_SEC)
        self.interval.setValue(settings.autosave_interval_sec)
        self.interval.setSuffix(" 秒")

        self.quality = QSpinBox(self)
        self.quality.setRange(JPG_QUALITY_MIN, JPG_QUALITY_MAX)
        self.quality.setValue(settings.jpg_quality)

        self.opacity = QDoubleSpinBox(self)
        self.opacity.setRange(ROUGH_OPACITY_MIN, ROUGH_OPACITY_MAX)
        self.opacity.setDecimals(2)
        self.opacity.setSingleStep(ROUGH_OPACITY_STEP)
        self.opacity.setValue(settings.rough_opacity)

        self.form = QFormLayout()
        form = self.form
        # **項目と項目の間を1行ぶん空ける。** 説明が入力欄の真下に数行続くので、
        # 既定の詰まった間隔だと、下の説明が次の項目の見出しとくっついて
        # 見え、どこまでが1つの項目なのか読み取れない（本人の指摘 2026-08-06）。
        # 固定の px ではなく文字の高さから取るのは、表示の拡大率や文字の
        # 大きさが変わっても「1行ぶん」であり続けるようにするため
        form.setVerticalSpacing(self.fontMetrics().height())
        form.addRow(
            "最初に開くフォルダ",
            self._field(
                picker,
                "保存や画像選びの画面が最初に開く場所。作品フォルダそのものではなく、"
                "その1つ上を指します（既定: ドキュメント）",
                self.folder_note,
            ),
        )
        form.addRow(
            "自動バックアップの間隔",
            self._field(
                self.interval,
                f"既定 {AUTOSAVE_INTERVAL_DEFAULT_SEC} 秒（5分）。"
                "この項目だけは、変えたらアプリを開き直します",
            ),
        )
        form.addRow(
            "JPG の品質",
            self._field(
                self.quality,
                f"既定 {JPG_QUALITY_DEFAULT}。大きいほど高画質・大容量。"
                "90 と 100 は見分けがつかず、容量だけが倍ほど増えます",
            ),
        )
        form.addRow(
            "ラフの濃さ",
            self._field(
                self.opacity,
                f"既定 {ROUGH_OPACITY_DEFAULT:.2f}。小さいほど薄い。"
                "薄いとコマ枠と紛れず、濃いとなぞる線がよく見えます",
            ),
        )
        return form

    def _field(self, widget, note: str, extra: QLabel | None = None) -> QWidget:
        """入力欄と、その下の説明を1つにまとめる。

        説明を欄の右や別の窓（ヘルプ）に置かず真下に置くのは、**書き換える
        瞬間に読む言葉**だから。README を開き直さずに済ませるのがこの道具の
        目的の1つなので、離すと目的が半分になる。
        """
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        if isinstance(widget, QWidget):
            box.addWidget(widget)
        else:
            box.addLayout(widget)
        label = QLabel(note, self)
        label.setWordWrap(True)
        box.addWidget(label)
        if extra is not None:
            box.addWidget(extra)
        field = QWidget(self)
        field.setLayout(box)
        return field

    def _buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(self)
        save = buttons.addButton("保存して閉じる", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("やめる", QDialogButtonBox.ButtonRole.RejectRole)
        save.setDefault(True)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        return buttons

    # -- 動き ----------------------------------------------------------------

    def _choose_folder(self) -> None:
        """ファイル窓から選ぶ。今の値が実在すればそこから始める。"""
        current = normalized_dir(self.folder.text())
        start = current if current and pathlib.Path(current).is_dir() else ""
        chosen = QFileDialog.getExistingDirectory(self, "最初に開くフォルダを選ぶ", start)
        if chosen:
            self.folder.setText(str(pathlib.Path(chosen)))

    def _update_folder_note(self) -> None:
        folder = normalized_dir(self.folder.text())
        missing = bool(folder) and not pathlib.Path(folder).is_dir()
        self.folder_note.setText(
            "この場所は今この PC からは見えません（外付けドライブが繋がって"
            "いない可能性）。このまま保存はできますが、見えない間はドキュメント"
            "から始まります"
            if missing
            else ""
        )
        self.folder_note.setVisible(missing)

    def chosen(self) -> AppSettings:
        """今、画面に入っている設定。"""
        return AppSettings(
            default_parent_dir=normalized_dir(self.folder.text()),
            autosave_interval_sec=self.interval.value(),
            jpg_quality=self.quality.value(),
            rough_opacity=round(self.opacity.value(), 2),
        )

    def save(self) -> None:
        """書き戻して閉じる。**書けなかったときは閉じない。**

        閉じてしまうと、書けなかったことに気づかないまま「設定した」と
        思い込む。設定は次にアプリを使うまで結果が見えないので、ここで
        止めないと気づく機会が無い。
        """
        try:
            update_settings_file(self.chosen(), self.path)
        except OSError as error:
            QMessageBox.warning(self, "設定を保存できません", f"{self.path}\n\n{error}")
            return
        self.accept()


def main() -> int:
    app = QApplication(sys.argv)
    editor = SettingsEditor()
    editor.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
