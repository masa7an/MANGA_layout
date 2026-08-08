"""書き出しの「別名で書いて置き換える」の共有部分の検証。

以前は PNG/JPG（`ui.export.write_image`）と PSD（`psd.write_psd`）で
この置き換え処理が一字一句そのまま重複していた。片方だけ直る危険を
避けるため、共有の1関数（`export_io.replace_or_raise`）へ集約した
（2026-08-08）。
"""

from __future__ import annotations

import pytest

from manga_layout.errors import ExportError
from manga_layout.export_io import replace_or_raise, tmp_path_for


def test_仮の名前はtmpが付く(tmp_path):
    path = tmp_path / "p01.png"
    assert tmp_path_for(path) == tmp_path / "p01.png.tmp"


def test_書けていれば入れ替わる(tmp_path):
    path = tmp_path / "p01.png"
    tmp = tmp_path_for(path)
    tmp.write_bytes(b"abc")

    replace_or_raise(tmp, path)

    assert path.read_bytes() == b"abc"
    assert not tmp.exists()


def test_置き換えに失敗すると分かるエラーになる(tmp_path, monkeypatch):
    """他のアプリで開いたままなど、`os.replace` 自体が失敗する場合。"""
    import manga_layout.export_io as export_io_module

    path = tmp_path / "p01.png"
    tmp = tmp_path_for(path)
    tmp.write_bytes(b"abc")

    def 失敗するreplace(src, dst):
        raise OSError("模擬した置き換え失敗")

    monkeypatch.setattr(export_io_module.os, "replace", 失敗するreplace)

    with pytest.raises(ExportError, match="置き換えられませんでした"):
        replace_or_raise(tmp, path)


def test_失敗したらtmpを残さない(tmp_path, monkeypatch):
    import manga_layout.export_io as export_io_module

    path = tmp_path / "p01.png"
    tmp = tmp_path_for(path)
    tmp.write_bytes(b"abc")

    monkeypatch.setattr(
        export_io_module.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("x"))
    )

    with pytest.raises(ExportError):
        replace_or_raise(tmp, path)

    assert not tmp.exists()
