"""assets/ の管理（SHA1 による重複排除）の検証。"""

from __future__ import annotations

import pytest

from manga_layout import AssetStore, sniff_format
from manga_layout.assets import PendingAssets, ref_for
from manga_layout.errors import AssetError, UnknownImageFormatError


class TestSniff:
    def test_形式を見分ける(self, fixture_dir):
        assert sniff_format((fixture_dir / "rgba_transparent.png").read_bytes()) == "png"
        assert sniff_format(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "jpg"
        assert sniff_format(b"GIF89a" + b"\x00" * 20) == "gif"
        assert sniff_format(b"BM" + b"\x00" * 20) == "bmp"
        assert sniff_format(b"RIFF\x00\x00\x00\x00WEBP") == "webp"

    def test_画像でないものは分からないと答える(self):
        assert sniff_format(b"this is a text file") is None
        assert sniff_format(b"") is None

    def test_署名だけ正しい壊れたファイルは見抜けない(self, fixture_dir):
        # fixtures/broken.png は PNG の署名を持つが中身が壊れている。
        # この層はバイト列しか見ないので通ってしまう。
        # 復号による検証は取り込み側（QImage を使う層）の責務。
        broken = (fixture_dir / "broken.png").read_bytes()
        assert sniff_format(broken) == "png"


class TestAdd:
    def test_取り込むと参照が返る(self, tmp_path, png_bytes):
        store = AssetStore(tmp_path)
        ref = store.add_bytes(png_bytes)
        assert ref.startswith("assets/")
        assert ref.endswith(".png")
        assert store.exists(ref)
        assert store.read(ref) == png_bytes

    def test_中身が同じなら実体は1つ(self, tmp_path, fixture_dir):
        # fixtures/dup_source.png と dup_copy.png は名前だけ違う同じ画像
        store = AssetStore(tmp_path)
        ref_a = store.add_file(fixture_dir / "dup_source.png")
        ref_b = store.add_file(fixture_dir / "dup_copy.png")

        assert ref_a == ref_b
        assert len(store.list_refs()) == 1

    def test_中身が違えば別々に入る(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        store.add_file(fixture_dir / "rgba_transparent.png")
        store.add_file(fixture_dir / "rgb_opaque.png")
        assert len(store.list_refs()) == 2

    def test_同じものを2度入れても書き直さない(self, tmp_path, png_bytes):
        store = AssetStore(tmp_path)
        ref = store.add_bytes(png_bytes)
        stat_before = store.resolve(ref).stat().st_mtime_ns
        store.add_bytes(png_bytes)
        assert store.resolve(ref).stat().st_mtime_ns == stat_before

    def test_画像でないものは拒む(self, tmp_path):
        store = AssetStore(tmp_path)
        with pytest.raises(UnknownImageFormatError):
            store.add_bytes(b"not an image at all")
        with pytest.raises(UnknownImageFormatError):
            store.add_bytes(b"")

    def test_読めないファイルは分かるメッセージで落ちる(self, tmp_path):
        store = AssetStore(tmp_path)
        with pytest.raises(AssetError, match="読めませんでした"):
            store.add_file(tmp_path / "ない.png")

    def test_書き込み途中のファイルを残さない(self, tmp_path, png_bytes):
        store = AssetStore(tmp_path)
        store.add_bytes(png_bytes)
        assert list(store.dir.glob("*.tmp")) == []


class TestResolve:
    def test_フォルダの外を指す参照を拒む(self, tmp_path):
        # 参照は project.json 由来＝外から来た文字列なので信用しない
        store = AssetStore(tmp_path)
        for bad in ("assets/../../秘密.txt", "../assets/a.png", "/etc/passwd", "assets/"):
            with pytest.raises(AssetError):
                store.resolve(bad)

    def test_区切りが円記号でも同じに扱う(self, tmp_path, png_bytes):
        store = AssetStore(tmp_path)
        ref = store.add_bytes(png_bytes)
        assert store.resolve(ref.replace("/", "\\")) == store.resolve(ref)

    def test_無い参照は存在しないと答える(self, tmp_path):
        store = AssetStore(tmp_path)
        assert not store.exists("assets/deadbeef.png")
        assert not store.exists("壊れた参照")


class TestCollectUnused:
    def test_参照されていないものを退避する(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        keep = store.add_file(fixture_dir / "rgba_transparent.png")
        drop = store.add_file(fixture_dir / "rgb_opaque.png")

        moved = store.collect_unused({keep})

        assert moved == [drop]
        assert store.exists(keep)
        assert not store.exists(drop)
        # 削除ではなく移動。判断を誤っても戻せる
        assert (store.unused_dir / store.resolve(drop).name).is_file()

    def test_全部使われていれば何も動かさない(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        refs = {
            store.add_file(fixture_dir / "rgba_transparent.png"),
            store.add_file(fixture_dir / "rgb_opaque.png"),
        }
        assert store.collect_unused(refs) == []
        assert len(store.list_refs()) == 2

    def test_退避先に同名があっても落ちない(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        ref = store.add_file(fixture_dir / "rgb_opaque.png")
        store.collect_unused(set())
        # 同じ画像をもう一度取り込んで、また未使用にする
        store.add_file(fixture_dir / "rgb_opaque.png")
        assert store.collect_unused(set()) == [ref]
        assert not store.exists(ref)


class TestPending:
    """保存先が決まる前に貼り付けた画像の預かり所。"""

    def test_預けた参照は保存後と同じ(self, tmp_path, png_bytes):
        # ここが食い違うと、保存した瞬間に project.json の参照が
        # 実体を指さなくなる
        pending = PendingAssets()
        assert pending.add(png_bytes) == AssetStore(tmp_path).add_bytes(png_bytes)

    def test_預けた中身を取り出せる(self, png_bytes):
        pending = PendingAssets()
        ref = pending.add(png_bytes)
        assert ref in pending
        assert pending.get(ref) == png_bytes
        assert pending.get("assets/none.png") is None

    def test_書き出すと実体になる(self, tmp_path, png_bytes):
        pending = PendingAssets()
        ref = pending.add(png_bytes)
        store = AssetStore(tmp_path)

        assert pending.flush_to(store) == [ref]

        assert store.exists(ref)
        assert store.read(ref) == png_bytes

    def test_書き出しただけでは控えは空にならない(self, tmp_path, png_bytes):
        """空にするのは呼ぶ側が `clear()` で明示したときだけ。

        `EditorState.save` は、この直後に project.json を書く。そちらが
        失敗した場合に備えて控えを残しておかないと、失敗を跨いで控えが
        消え、別の場所へ保存し直しても実体が書かれない（2026-08-08 に発見）。
        """
        pending = PendingAssets()
        pending.add(png_bytes)
        store = AssetStore(tmp_path)

        pending.flush_to(store)

        assert len(pending) == 1

    def test_続けて呼んでも書き直さない(self, tmp_path, png_bytes):
        """控えを持ったまま呼び直せる（失敗しての再試行を想定）。

        内容ハッシュ名なので、既にある実体への2回目の書き込みは
        `add_bytes` の側で無視される（→ 重複や余計な書き込みが起きない）。
        """
        pending = PendingAssets()
        ref = pending.add(png_bytes)
        store = AssetStore(tmp_path)

        pending.flush_to(store)
        written_again = pending.flush_to(store)

        assert written_again == [ref]
        assert store.read(ref) == png_bytes

    def test_clearで控えを手放せる(self, tmp_path, png_bytes):
        pending = PendingAssets()
        pending.add(png_bytes)
        store = AssetStore(tmp_path)
        pending.flush_to(store)

        pending.clear()

        assert len(pending) == 0

    def test_参照が無くなったものも書き出す(self, tmp_path, fixture_dir):
        """Undo で戻せば復活するので、保存時点の参照だけで捨てない。"""
        pending = PendingAssets()
        kept = pending.add((fixture_dir / "rgba_transparent.png").read_bytes())
        dropped = pending.add((fixture_dir / "rgb_opaque.png").read_bytes())
        store = AssetStore(tmp_path)

        pending.flush_to(store)

        assert store.exists(kept) and store.exists(dropped)

    def test_画像でないものは預からない(self):
        with pytest.raises(UnknownImageFormatError):
            PendingAssets().add(b"this is a text file")


class TestRefFor:
    def test_書き込まずに参照を決められる(self, tmp_path, png_bytes):
        ref = ref_for(png_bytes)
        assert ref.startswith("assets/") and ref.endswith(".png")
        assert not (tmp_path / ref).exists()

    def test_空や画像でないものは断る(self):
        with pytest.raises(UnknownImageFormatError):
            ref_for(b"")
        with pytest.raises(UnknownImageFormatError):
            ref_for(b"not an image")
