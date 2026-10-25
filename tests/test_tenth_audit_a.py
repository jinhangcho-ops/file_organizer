"""열 번째 감사(A)에서 막은 것: 정션 경로로 적힌 보호 목록(차단·구조 보호·잠금·사용자 폴더)이 실제 경로의 색인과 어긋나던 것,
정션인 놓을 곳으로 옮긴 뒤의 잠금 기록, 정션 경로로 저장된 메모·예외, 여러 개를 합치면 휴지통보다 큰 보내기, 보관 중에 클라우드 전용이 된 파일.
파일은 모두 지어낸다. 실제 휴지통은 쓰지 않는다(fake_trash, 또는 trash.problem만 부른다)."""
import json
import os
import subprocess
import time

import pytest

import actions
import archive
import blacklist
import drives
import exclude
import fence
import index
import lock
import notes
import protect
import settings
import trash
from conftest import age, build, finish, write


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def indexed_keys():
    db = index.connect()
    try:
        return {row[0] for row in db.execute("select key from files")}
    finally:
        db.close()


def old_list(name, paths):
    """예전 판이 정션 경로 그대로 적어 둔 관리자 목록."""
    folder = blacklist.folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps([str(p) for p in paths]), encoding="utf-8")


def test_profile_reached_through_junction_is_not_moved(home, tmp_path, monkeypatch, client):
    """C:\\Users가 D:\\Users를 가리키는 정션인 PC: ProfileList·환경 변수는 정션 경로, 색인은 실제 경로다."""
    real_users, users = tmp_path / "real_users", tmp_path / "users"
    me = real_users / "me"
    write(me / "문서" / "a.txt")
    (me / "AppData" / "Local").mkdir(parents=True)
    junction(users, real_users)
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(users / "me" / "AppData" / "Local"))
        monkeypatch.setattr(drives, "profile_folders", lambda: [str(users / "me")])
        settings.save({"roots": [str(home), str(real_users)]})
        build(home)
        with pytest.raises(ValueError, match="Windows·프로그램 폴더가 들어 있어"):
            fence.refuse_holding(str(me), True, moving=True)
        assert client.post("/api/move", json={"paths": [str(me)], "folder": str(home)}).status_code == 400
        assert (me / "AppData").is_dir()
    finally:
        os.rmdir(users)


def test_old_blacklist_entry_by_junction_path_still_blocks(home, tmp_path):
    target, link = tmp_path / "real", tmp_path / "link"
    write(target / "secret" / "a.txt")
    write(target / "plain.txt")
    junction(link, target)
    try:
        old_list(blacklist.FILE, [link / "secret"])
        settings.save({"roots": [str(link)]})
        build(home)
        assert not any("secret" in k for k in indexed_keys()) and any("plain" in k for k in indexed_keys())
        with pytest.raises(ValueError, match="차단하거나 잠근"):
            fence.refuse(str(target / "secret" / "a.txt"))
    finally:
        os.rmdir(link)


def test_old_protected_entry_by_junction_path_keeps_old_files(home, tmp_path):
    target, link = tmp_path / "real", tmp_path / "link"
    old = write(target / "kept" / "old.txt")
    junction(link, target)
    try:
        old_list(protect.FILE, [link / "kept"])
        time.sleep(1.5)  # 새 파일의 연 시각을 백신 검사가 바꾼 뒤에 날짜를 바꾼다
        age(old, 400)
        settings.save({"roots": [str(link)]})
        build(home)
        assert any("old.txt" in k for k in indexed_keys())
        assert archive.candidates(30) == []
    finally:
        os.rmdir(link)


def test_open_lock_folder_recorded_by_junction_path_is_not_indexed(home, tmp_path):
    target, link = tmp_path / "real", tmp_path / "link"
    write(target / "locked" / "a.txt")
    junction(link, target)
    try:
        lock.save({"hash": None, "salt": None, "vault": "", "vault_open": False, "folders": [{"path": str(link / "locked"), "open": True}]})
        settings.save({"roots": [str(link)]})
        build(home)
        assert not any("locked" in k for k in indexed_keys())
    finally:
        os.rmdir(link)


def test_network_drive_entry_keeps_its_letter(home, tmp_path, monkeypatch):
    """Z:가 \\\\srv\\share인 네트워크 드라이브: realpath는 UNC로 바꾸지만 색인은 Z:로 한다(index.real)."""
    drive = tmp_path / "z"
    (drive / "secret").mkdir(parents=True)
    (drive / "kept").mkdir()
    real = os.path.realpath

    def unc(path, *args, **kwargs):
        p = os.path.abspath(path)
        if exclude.key(p).startswith(exclude.key(drive)):
            return "\\\\srv\\share" + p[len(str(drive)):]
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", unc)
    monkeypatch.setattr(index, "real", os.path.abspath)  # index.real은 드라이브 글자를 둔다(그 판별은 index의 검사가 본다)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert blacklist.add(str(drive / "secret"), set()) == [str(drive / "secret")]
    assert protect.add(str(drive / "kept")) == [str(drive / "kept")]


@pytest.fixture
def junction_dest(home, tmp_path):
    """root\\X 안에 열어 둔 잠금 폴더 L, 놓을 곳 root\\dst와 그것을 가리키는 정션 jdst."""
    write(home / "X" / "L" / "secret.txt")
    write(home / "X" / "other.txt")
    (home / "dst").mkdir()
    jdst = tmp_path / "jdst"
    junction(jdst, home / "dst")
    build(home)
    lock.save({"hash": None, "salt": None, "vault": "", "vault_open": False, "folders": [{"path": str(home / "X" / "L"), "open": True}]})
    yield jdst
    os.rmdir(jdst)


def test_folder_text_gives_real_path(junction_dest, home):
    assert settings.folder_text(str(junction_dest), "놓을 곳") == str(home / "dst")


def test_move_into_junction_destination_keeps_lock_record(junction_dest, home, client, fake_trash):
    notes.put(str(home / "X"), "메모")
    assert client.post("/api/move", json={"paths": [str(home / "X")], "folder": str(junction_dest)}).status_code == 200
    finish()
    moved = home / "dst" / "X"
    assert lock.folders() == [str(moved / "L")] and notes.get(str(moved)) == "메모"
    assert client.post("/api/trash", json={"paths": [str(moved)], "confirm": True}).status_code == 400
    assert fake_trash == [] and (moved / "L" / "secret.txt").exists()


def test_moved_with_junction_pair_records_real_paths(junction_dest, home):
    """옛 되돌리기 기록 등 정션 경로로 알려 온 (전, 후)도 실제 경로로 반영한다."""
    os.rename(home / "X", home / "dst" / "X")
    actions.moved([(str(home / "X"), str(junction_dest / "X"))])
    assert lock.folders() == [str(home / "dst" / "X" / "L")]


def test_old_note_and_exclude_by_junction_path_are_found(home, tmp_path):
    target, link = tmp_path / "real", tmp_path / "link"
    write(target / "폴더" / "a.txt")
    junction(link, target)
    try:
        old_note = str(link / "폴더")
        settings.write(notes.FILE, {exclude.key(old_note): {"path": old_note, "text": "옛 메모"}})
        settings.write(exclude.FILE, [{"path": old_note, "kind": "folder"}, {"path": str(link / "폴더" / "a.txt"), "kind": "file"}])
        assert notes.get(str(target / "폴더")) == "옛 메모"
        assert [r["path"] for r in exclude.load()] == [str(target / "폴더"), str(target / "폴더" / "a.txt")]
        assert [r["path"] for r in settings.read(exclude.FILE, [])] == [str(target / "폴더"), str(target / "폴더" / "a.txt")]  # 바꾼 것을 저장했다
    finally:
        os.rmdir(link)


def test_items_together_bigger_than_recycle_bin_are_refused(tmp_path, monkeypatch, fake_trash):
    paths = [write(tmp_path / n, "x" * 600) for n in "ab"]
    monkeypatch.setattr(trash, "disk", lambda place: (None, 1000))  # 휴지통 최대 1,000바이트(지어낸 값)
    sent, refused = trash.to_trash(paths, {})
    assert sent == [paths[0]] and refused == [(paths[1], "휴지통 최대 크기보다 큽니다")]
    assert trash.problem(paths[1]) is None  # 혼자면 들어간다


def test_archive_skips_file_that_became_cloud_only(home, tmp_path, monkeypatch, fake_trash):
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))
    old = write(home / "보고" / "오래된.txt", "가나다" * 100)
    time.sleep(1.5)
    age(old, 400)
    build(home)
    dest = tmp_path / "보관함"
    dest.mkdir()
    monkeypatch.setattr(archive, "cloud", lambda path: False)  # 고를 때는 내려받은 파일이었다
    monkeypatch.setattr(index, "cloud_only", lambda st: True)  # zip을 만드는 동안 온라인 전용이 됐다
    with pytest.raises(ValueError, match="zip에 넣은 파일이 없습니다"):
        archive.run(str(dest), 365)
    assert os.path.exists(old) and all(p.endswith(".zip") for p in fake_trash)
