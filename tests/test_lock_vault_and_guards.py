"""감사 뒤 고친 것들: 차단·잠금 폴더를 품은 위 폴더, 앱 안에서 옮기면 잠금 위치 고치기, 금고로 모으기·열기·꺼내기,
비밀 파일(lock.dat) 옮기기, 색인 중 차단·잠금 거부, 사라진 잠금 폴더, 휴지통 크기 값이 없는 디스크, 읽기에 실패한 파일 다시 읽기.
잠금은 임시 폴더에서 실제 icacls로 건다(끝나면 unlock_after가 모두 푼다). 비밀번호는 지어낸 값이다."""
import collections
import json
import os

import pytest

import blacklist
import extract
import index
import lock
import server
import settings
import trash
from conftest import build, write
from test_blocked_and_locked_folders import PASSWORD


@pytest.fixture
def locked(home, client):
    client.post("/api/lock/password", json={"new": PASSWORD})
    return home


def test_parent_of_blocked_folder_is_never_moved_or_trashed(home, client, monkeypatch, fake_trash):
    write(home / "Games" / "SteamLibrary" / "game.dat")
    write(home / "Games" / "메모.txt")
    other = home / "다른 곳"
    other.mkdir()
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(home / "Games" / "SteamLibrary")})
    games = str(home / "Games")
    assert client.post("/api/trash", json={"paths": [games], "confirm": True}).status_code == 400
    assert client.post("/api/move", json={"paths": [games], "folder": str(other)}).status_code == 400
    assert client.post("/api/rename", json={"path": games, "name": "게임"}).status_code == 400
    assert fake_trash == [] and os.path.isdir(home / "Games" / "SteamLibrary")


def test_parent_of_locked_folder_moves_only_on_same_drive_and_lock_follows(locked, client, monkeypatch, fake_trash, unlock_after):
    write(locked / "가" / "비밀" / "a.txt")
    write(locked / "가" / "b.txt")
    (locked / "다른 곳").mkdir()
    build(locked)
    secret = str(locked / "가" / "비밀")
    assert client.post("/api/lock", json={"path": secret, "password": PASSWORD}).status_code == 200
    parent = str(locked / "가")
    assert client.post("/api/trash", json={"paths": [parent], "confirm": True}).status_code == 400  # 비우면 잠긴 내용까지 사라진다
    with monkeypatch.context() as m:  # undo()는 home의 APPDATA 바꾸기까지 되돌려 실제 설정을 건드린다. 이 안의 것만 되돌린다.
        m.setattr(server.drives, "same_drive", lambda a, b: False)
        assert client.post("/api/move", json={"paths": [parent], "folder": str(locked / "다른 곳")}).status_code == 400  # 다른 디스크
    renamed = client.post("/api/rename", json={"path": parent, "name": "나"}).json["path"]
    assert [f["path"] for f in lock.listing()] == [os.path.join(renamed, "비밀")]  # 비밀 파일의 위치가 따라간다
    job = client.post("/api/move", json={"paths": [renamed], "folder": str(locked / "다른 곳")}).json["job"]
    assert job and server.jobs.wait()["result"]["moved"] == 1
    moved = str(locked / "다른 곳" / "나" / "비밀")
    assert lock.listing() == [{"path": moved, "open": False, "vault": False, "missing": False}]
    with pytest.raises(PermissionError):
        os.listdir(moved)  # 옮겨도 잠긴 그대로
    assert client.post("/api/lock/release", json={"path": moved, "password": PASSWORD}).status_code == 200
    assert os.listdir(moved) == ["a.txt"]


def test_vault_collect_open_take_out(locked, client, monkeypatch, unlock_after):
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    write(locked / "사진" / "여행" / "1.jpg", "x")
    build(locked)
    trip = str(locked / "사진" / "여행")
    assert client.post("/api/lock", json={"path": trip, "password": PASSWORD, "vault": True}).status_code == 400  # 금고 전
    place = locked.parent / "금고 둘 곳"
    place.mkdir()
    vault = client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)}).json["vault"]
    assert vault == str(place / ".vault") and os.stat(vault).st_file_attributes & 0x6 == 0x6
    assert client.post("/api/lock", json={"path": trip, "password": PASSWORD, "vault": True}).json["locks"] == [
        {"path": trip, "open": False, "vault": True, "missing": False}]
    assert not os.path.exists(trip)  # 원래 자리에서 사라졌다
    with pytest.raises(PermissionError):
        os.listdir(vault)
    assert client.post("/api/lock/open", json={"path": trip, "password": PASSWORD}).json["locks"][0]["open"] is True
    inside = opened[0]
    assert os.path.dirname(inside) == vault and os.path.basename(inside) != "여행" and os.listdir(inside) == ["1.jpg"]  # 무작위 이름
    lock.close_all()
    with pytest.raises(PermissionError):
        os.listdir(vault)
    assert client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)}).status_code == 400  # 든 것이 있으면 금고를 못 바꾼다
    os.mkdir(trip)  # 원래 자리에 같은 이름이 생겼다
    assert client.post("/api/lock/take_out", json={"path": trip, "password": PASSWORD}).json["path"] == trip + " (2)"
    assert os.listdir(trip + " (2)") == ["1.jpg"] and lock.listing() == []
    with pytest.raises(PermissionError):
        os.listdir(vault)  # 꺼낸 뒤에도 금고는 잠긴 그대로


def test_take_out_asks_for_a_folder_when_the_parent_is_gone(locked, client, monkeypatch, unlock_after):
    write(locked / "위" / "모음" / "a.txt")
    build(locked)
    place = locked.parent / "금고"
    place.mkdir()
    client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)})
    item = str(locked / "위" / "모음")
    client.post("/api/lock", json={"path": item, "password": PASSWORD, "vault": True})
    os.rmdir(locked / "위")  # 원래 위 폴더가 사라졌다
    assert "need_folder" in client.post("/api/lock/take_out", json={"path": item, "password": PASSWORD}).json
    target = locked / "새 자리"
    target.mkdir()
    assert client.post("/api/lock/take_out", json={"path": item, "password": PASSWORD, "folder": str(target)}).json["path"] == str(target / "모음")


def test_collect_refuses_another_drive(locked, client, monkeypatch, unlock_after):
    write(locked / "모음" / "a.txt")
    build(locked)
    place = locked.parent / "금고"
    place.mkdir()
    client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)})
    monkeypatch.setattr(lock.drives, "same_drive", lambda a, b: False)
    answer = client.post("/api/lock", json={"path": str(locked / "모음"), "password": PASSWORD, "vault": True})
    assert answer.status_code == 400 and "다른 디스크" in answer.json["error"]
    assert os.path.isdir(locked / "모음") and lock.listing() == []  # 실패하면 기록도 남지 않는다


def test_old_lock_json_moves_into_secret_file(home):
    old = settings.folder() / "lock.json"
    old.write_text(json.dumps({"hash": "ab", "salt": "cd", "folders": [{"path": str(home), "open": False}]}), encoding="utf-8")
    assert lock.load()["folders"] == [{"path": str(home), "open": False}]
    assert not old.exists() and (settings.folder() / "lock.dat").exists()


def test_no_changes_while_indexing(locked, client, monkeypatch):
    write(locked / "가" / "a.txt")
    build(locked)
    monkeypatch.setitem(index.status, "running", True)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert "색인하는 중" in client.post("/api/lock", json={"path": str(locked / "가"), "password": PASSWORD}).json["error"]
    assert "색인하는 중" in client.post("/api/blacklist", json={"path": str(locked / "가")}).json["error"]


def test_missing_locked_folder_can_be_released(locked, client, unlock_after):
    write(locked / "가" / "a.txt")
    build(locked)
    box = str(locked / "가")
    client.post("/api/lock", json={"path": box, "password": PASSWORD})
    lock.unseal(box)
    os.rename(box, str(locked / "옮김"))  # 앱 밖에서 옮겼다
    assert lock.listing()[0]["missing"] is True
    assert client.post("/api/lock/release", json={"path": box, "password": PASSWORD}).json["locks"] == []


def test_unknown_recycle_bin_size_uses_five_percent_of_disk(home, monkeypatch, fake_trash):
    big = write(home / "큰 파일.bin", "x" * 100)
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))  # 설정 값이 없는 디스크
    monkeypatch.setattr(trash.shutil, "disk_usage", lambda path: collections.namedtuple("u", "total used free")(1000, 0, 1000))
    assert trash.problem(big) == "휴지통 최대 크기보다 큽니다"  # 1000바이트의 5% = 50 < 100
    assert trash.to_trash([big]) == ([], [(big, "휴지통 최대 크기보다 큽니다")]) and fake_trash == []


def test_failed_read_is_tried_again(home, client, monkeypatch):
    note = write(home / "메모.txt", "다시 읽을 글")

    def fail(path):
        raise OSError("잠시 잠김")

    with monkeypatch.context() as m:  # undo()를 쓰지 않는다(위와 같은 까닭)
        m.setitem(extract.READERS, ".txt", fail)
        build(home)
        assert client.get("/api/preview?path=" + note).json["text"] == ""
    build(home)
    assert client.get("/api/preview?path=" + note).json["text"] == "다시 읽을 글"
