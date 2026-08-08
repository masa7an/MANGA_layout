"""project.json の読み書きの検証。"""

from __future__ import annotations

import json

import pytest

from manga_layout import (
    AssetStore,
    Project,
    Rect,
    find_missing_assets,
    is_project_dir,
    load_project,
    new_project,
    prune_unused_assets,
    save_project,
)
from manga_layout.errors import ProjectFormatError, ProjectNotFoundError
from manga_layout.storage import (
    AUTOSAVE_GENERATIONS,
    BACKUP_DIRNAME,
    BACKUP_GENERATIONS,
    PROJECT_FILENAME,
    atomic_write_text,
    write_autosave,
)


class TestSaveLoad:
    def test_保存して読み戻すと一致する(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        restored = load_project(tmp_path)
        assert restored.to_dict() == sample_project.to_dict()

    def test_保存でフォルダ構成が作られる(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path / "作品")
        assert (tmp_path / "作品" / PROJECT_FILENAME).is_file()
        assert (tmp_path / "作品" / "assets").is_dir()
        assert is_project_dir(tmp_path / "作品")

    def test_日本語がそのまま読める形で書かれる(self, tmp_path, sample_project):
        # エスケープされた JSON は、中身を確認したいときに読めない
        save_project(sample_project, tmp_path)
        text = (tmp_path / PROJECT_FILENAME).read_text(encoding="utf-8")
        assert "テスト作品" in text
        assert "\\u30c6" not in text

    def test_改行はLFで書かれる(self, tmp_path, sample_project):
        # 2台のPC間で git の差分が出ないようにする
        save_project(sample_project, tmp_path)
        raw = (tmp_path / PROJECT_FILENAME).read_bytes()
        assert b"\r\n" not in raw

    def test_書き込み途中のファイルを残さない(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_置き換え自体が失敗してもtmpを残さない(self, tmp_path, monkeypatch):
        """`os.replace` が失敗する場合（他アプリに掴まれているなど）。

        以前は書き切った `.tmp` の後始末が無く、失敗のたびに作品フォルダへ
        残骸が積み上がっていた（2026-08-08 に発見）。
        """
        import manga_layout.storage as storage_module

        path = tmp_path / "project.json"

        def 失敗するreplace(src, dst):
            raise OSError("模擬した置き換え失敗")

        monkeypatch.setattr(storage_module.os, "replace", 失敗するreplace)

        with pytest.raises(OSError):
            atomic_write_text(path, "内容")

        assert list(tmp_path.glob("*.tmp")) == []

    def test_座標は1行に畳まれる(self, tmp_path, sample_project):
        # 1座標ごとに改行すると、コマが数百あるファイルを人が追えなくなる
        save_project(sample_project, tmp_path)
        text = (tmp_path / PROJECT_FILENAME).read_text(encoding="utf-8")
        assert "[10.0, 10.0]" in text
        assert "[1200, 900]" in text

    def test_改行を含むセリフを壊さない(self, tmp_path, sample_project):
        # 1行に畳む処理が、文字列の中身まで書き換えていないこと
        text_obj = next(f for f in sample_project.pages[0].floating if hasattr(f, "content"))
        text_obj.content = "行1 [1,\n2] 行2"

        save_project(sample_project, tmp_path)

        assert load_project(tmp_path).pages[0].floating[1].content == "行1 [1,\n2] 行2"

    def test_半角スペースの並びを含むセリフを壊さない(self, tmp_path, sample_project):
        """1行に畳む正規表現は「空白」ではなく「生の改行を含む空白」にだけ効く。

        座標の区切りをただの `\\s+` にしていた版では、セリフの中に
        `[ 12, 34 ]` のような半角スペース区切りの並びがあると、その中身
        まで `[12, 34]` に黙って書き換わっていた（2026-08-08 に実機で発見）。
        JSON 文字列の中に生の改行は出てこない（`\\n` の2文字にエスケープ
        される）ので、区切りに生の改行を必須にすれば構造的に誤爆しない。
        """
        text_obj = next(f for f in sample_project.pages[0].floating if hasattr(f, "content"))
        text_obj.content = "ここは [ 12, 34 ] 参照"

        save_project(sample_project, tmp_path)

        assert load_project(tmp_path).pages[0].floating[1].content == "ここは [ 12, 34 ] 参照"

    def test_無いフォルダを開こうとすると分かる例外(self, tmp_path):
        with pytest.raises(ProjectNotFoundError):
            load_project(tmp_path / "ない")

    def test_壊れたJSONは場所を示して落ちる(self, tmp_path):
        (tmp_path / PROJECT_FILENAME).write_text('{"format_version": 1,,}', encoding="utf-8")
        with pytest.raises(ProjectFormatError) as exc:
            load_project(tmp_path)
        message = str(exc.value)
        assert "行目" in message
        # 復旧の手がかりを添える
        assert BACKUP_DIRNAME in message


class TestBackup:
    def test_上書き前の内容が退避される(self, tmp_path):
        project = new_project(title="1回目")
        save_project(project, tmp_path)

        project.title = "2回目"
        save_project(project, tmp_path)

        backup = tmp_path / BACKUP_DIRNAME / "project.1.json"
        assert json.loads(backup.read_text(encoding="utf-8"))["title"] == "1回目"
        assert load_project(tmp_path).title == "2回目"

    def test_世代が繰り下がる(self, tmp_path):
        project = new_project()
        for n in range(4):
            project.title = f"{n}回目"
            save_project(project, tmp_path)

        backup_dir = tmp_path / BACKUP_DIRNAME
        # 直前が .1、その前が .2
        assert json.loads((backup_dir / "project.1.json").read_text(encoding="utf-8"))["title"] == "2回目"
        assert json.loads((backup_dir / "project.2.json").read_text(encoding="utf-8"))["title"] == "1回目"

    def test_世代数を超えたら古いものが消える(self, tmp_path):
        project = new_project()
        for n in range(BACKUP_GENERATIONS + 3):
            project.title = f"{n}回目"
            save_project(project, tmp_path)

        backups = sorted((tmp_path / BACKUP_DIRNAME).glob("project.*.json"))
        assert len(backups) == BACKUP_GENERATIONS

    def test_初回保存では退避しない(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        assert not (tmp_path / BACKUP_DIRNAME).exists()

    def test_退避にtmpを残さない(self, tmp_path):
        project = new_project(title="1回目")
        save_project(project, tmp_path)
        project.title = "2回目"
        save_project(project, tmp_path)

        assert list((tmp_path / BACKUP_DIRNAME).glob("*.tmp")) == []

    def test_退避も別名で書いてから置き換える(self, tmp_path, monkeypatch):
        """複製の途中で落ちても、`project.1.json` が半端な内容にならない。

        以前は `shutil.copy2` で直接 `project.1.json` へ複製しており、
        途中で失敗すると切れた内容がその名前で残った（2026-08-08 に発見）。
        """
        import manga_layout.storage as storage_module

        project = new_project(title="1回目")
        save_project(project, tmp_path)

        real_copy2 = storage_module.shutil.copy2

        def 失敗するcopy2(src, dst):
            real_copy2(src, dst)
            raise OSError("模擬した複製失敗")

        monkeypatch.setattr(storage_module.shutil, "copy2", 失敗するcopy2)
        project.title = "2回目"
        with pytest.raises(OSError):
            save_project(project, tmp_path)

        # 複製先が半端な内容のまま project.1.json を名乗っていないこと
        dest = tmp_path / BACKUP_DIRNAME / "project.1.json"
        assert not dest.exists()

    def test_退避を止められる(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        save_project(sample_project, tmp_path, backup=False)
        assert not (tmp_path / BACKUP_DIRNAME).exists()


class TestAutosave:
    """タイマーからの自動バックアップ（要件定義 6.6）。"""

    def test_本体を書き換えない(self, tmp_path):
        # 押していない保存が起きると、未保存の確認と食い違う
        project = new_project(title="保存した内容")
        save_project(project, tmp_path)

        project.title = "作業中"
        write_autosave(project, tmp_path)

        assert load_project(tmp_path).title == "保存した内容"

    def test_作業中の内容が退避される(self, tmp_path):
        project = new_project(title="保存した内容")
        save_project(project, tmp_path)

        project.title = "作業中"
        path = write_autosave(project, tmp_path)

        assert json.loads(path.read_text(encoding="utf-8"))["title"] == "作業中"

    def test_保存の世代とは別の系列になる(self, tmp_path):
        """混ぜると、タイマーが回るたびに保存済みの世代が押し出されて消える。"""
        project = new_project(title="1回目")
        save_project(project, tmp_path)
        project.title = "2回目"
        save_project(project, tmp_path)

        for n in range(AUTOSAVE_GENERATIONS + 2):
            project.title = f"作業中{n}"
            write_autosave(project, tmp_path)

        backup_dir = tmp_path / BACKUP_DIRNAME
        saved = backup_dir / "project.1.json"
        assert json.loads(saved.read_text(encoding="utf-8"))["title"] == "1回目"

    def test_世代が繰り下がり_上限で古いものが消える(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        for n in range(AUTOSAVE_GENERATIONS + 2):
            sample_project.title = f"{n}回目"
            write_autosave(sample_project, tmp_path)

        backup_dir = tmp_path / BACKUP_DIRNAME
        files = sorted(backup_dir.glob("autosave.*.json"))
        assert len(files) == AUTOSAVE_GENERATIONS

        # 直前が .1、その前が .2
        latest = AUTOSAVE_GENERATIONS + 1
        assert json.loads((backup_dir / "autosave.1.json").read_text(encoding="utf-8"))["title"] == f"{latest}回目"
        assert json.loads((backup_dir / "autosave.2.json").read_text(encoding="utf-8"))["title"] == f"{latest - 1}回目"

    def test_退避した内容はそのまま読み戻せる(self, tmp_path, sample_project):
        # 復元は今のところ手作業（project.json へ置き換える）なので、
        # 中身が load_project で開ける形であることが要る
        save_project(sample_project, tmp_path)
        path = write_autosave(sample_project, tmp_path)

        (tmp_path / PROJECT_FILENAME).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        assert load_project(tmp_path).to_dict() == sample_project.to_dict()

    def test_書き込み途中のファイルを残さない(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        write_autosave(sample_project, tmp_path)
        assert list((tmp_path / BACKUP_DIRNAME).glob("*.tmp")) == []


class TestAssetsIntegration:
    def test_貼り付けから保存読み込みまで通る(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        ref = store.add_file(fixture_dir / "rgba_transparent.png")

        project = new_project(title="通し確認")
        page = project.pages[0]
        panel = project.add_panel(page, Rect(10.0, 10.0, 90.0, 60.0))
        project.add_image(panel, ref, Rect(10.0, 10.0, 64.0, 64.0), (64, 64))

        save_project(project, tmp_path)
        restored = load_project(tmp_path)

        assert restored.referenced_assets() == {ref}
        assert find_missing_assets(restored, tmp_path) == []
        assert AssetStore(tmp_path).read(ref) == (fixture_dir / "rgba_transparent.png").read_bytes()

    def test_実体が無い参照を報告する(self, tmp_path, sample_project):
        # 開くのは止めない。1枚欠けただけで作品全体が開けないのは割に合わない
        save_project(sample_project, tmp_path)
        restored = load_project(tmp_path)
        assert find_missing_assets(restored, tmp_path) == ["assets/abc123.png"]

    def test_未使用の整理は保存では起きない(self, tmp_path, fixture_dir):
        # Undo で戻したときに参照が切れるため、保存では触らない
        store = AssetStore(tmp_path)
        unused = store.add_file(fixture_dir / "rgb_opaque.png")

        project = new_project()
        save_project(project, tmp_path)

        assert store.exists(unused)

    def test_明示的に呼べば整理される(self, tmp_path, fixture_dir):
        store = AssetStore(tmp_path)
        used = store.add_file(fixture_dir / "rgba_transparent.png")
        unused = store.add_file(fixture_dir / "rgb_opaque.png")

        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(0.0, 0.0, 90.0, 60.0))
        project.add_image(panel, used, Rect(0.0, 0.0, 64.0, 64.0), (64, 64))
        save_project(project, tmp_path)

        assert prune_unused_assets(project, tmp_path) == [unused]
        assert store.exists(used)
        assert not store.exists(unused)


class TestRealisticProject:
    def test_多ページの作品を往復できる(self, tmp_path):
        project = new_project(title="30ページの読み切り")
        for _ in range(29):
            project.add_page()

        for page in project.pages:
            for row in range(3):
                panel = project.add_panel(project.pages[0], Rect(10.0, 10.0 + row * 90.0, 190.0, 85.0))
                project.add_balloon(page, Rect(20.0, 20.0, 40.0, 25.0), attached_panel_id=None)
                project.add_text(page, "セリフ", Rect(22.0, 22.0, 36.0, 21.0))
                assert panel.id

        save_project(project, tmp_path)
        restored = load_project(tmp_path)

        assert len(restored.pages) == 30
        assert restored.to_dict() == project.to_dict()
        assert restored.load_warnings == []

    def test_保存した内容を人が読める(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        data = json.loads((tmp_path / PROJECT_FILENAME).read_text(encoding="utf-8"))
        assert data["format_version"] == 2
        assert data["app"] == "MANGA_layout"
        assert data["pages"][0]["panels"][0]["shape"]["kind"] == "polygon"


class TestUndoFoundation:
    def test_スナップショットで元に戻せる(self, tmp_path, sample_project):
        # Day 5 の Undo が乗る土台。複製 → 変更 → 差し戻しが成立するか
        snapshot = sample_project.copy()

        page = sample_project.pages[0]
        page.move_panel(page.panels[0].id, 50.0, 50.0)
        sample_project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0))

        assert snapshot.to_dict() != sample_project.to_dict()

        save_project(snapshot, tmp_path)
        assert load_project(tmp_path).to_dict() == snapshot.to_dict()

    def test_保存に載らない項目は複製にも載らない(self, sample_project):
        # to_dict() への追加を忘れると、Undo でその項目だけ戻らない。
        # 複製を保存形式の往復で作っているので、ここで気づける
        sample_project.load_warnings.append("これは保存対象ではない")
        assert sample_project.copy().load_warnings == []


class TestProjectFromDictDirect:
    def test_最小限のJSONから開ける(self):
        # 手書きの project.json でも開けること
        project = Project.from_dict({"format_version": 2, "pages": []})
        assert project.pages == []
        assert project.reading_direction == "rtl"
        assert project.default_page_size.w == 1240.0
