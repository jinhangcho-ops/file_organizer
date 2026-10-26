"""차단 폴더(관리자만 바꿈, 색인·작업에서 완전히 뺌)와 잠금 폴더(Windows 거부 권한 + 숨김, 비밀번호로 열기), 폴더 용량.
잠금은 임시 폴더에서 실제 icacls로 걸고 푼다. 검사가 중간에 실패해도 끝나면 임시 폴더의 거부 권한을 모두 뗀다."""
import os

import pytest

import blacklist
import lock
import tree
from conftest import build, write

PASSWORD = "test-1234"  # 지어낸 시험용 비밀번호


def listed(client):
    return {row["path"] for row in client.post("/api/files", json={}).json["rows"]}


def test_blacklist_needs_admin_and_removes_from_everywhere(home, client, monkeypatch):
    keep = write(home / "문서" / "보고서.txt")
    game = write(home / "큰 폴더" / "game.dat")
    build(home)
    assert game in listed(client)
    monkeypatch.setattr(blacklist, "is_admin", lambda: False)  # 관리자로 도는 CI에서도 거부를 본다
    assert client.post("/api/blacklist", json={"path": str(home / "큰 폴더")}).status_code == 400  # 관리자가 아니면 거부
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert client.post("/api/blacklist", json={"path": str(home / "큰 폴더")}).status_code == 200
    assert listed(client) == {keep}  # 색인 기록을 바로 지웠다
    build(home)
    assert listed(client) == {keep}  # 다시 색인해도 들어가지 않는다
    assert client.post("/api/open", json={"path": game}).status_code == 400
    assert client.post("/api/settings", json={"roots": [str(home / "큰 폴더")]}).status_code == 400  # 그 안을 색인할 폴더로도 못 고른다
    assert client.post("/api/settings", json={"archive_dest": str(home / "큰 폴더")}).status_code == 400
    assert client.post("/api/blacklist", json={"path": str(home / "큰 폴더" / "x")}).status_code == 400  # 없는 폴더
    assert client.get("/api/state").json["blacklist"] == [str(home / "큰 폴더")]
    assert client.post("/api/blacklist", json={"path": str(home / "큰 폴더"), "remove": True}).json["blacklist"] == []
    build(home)
    assert game in listed(client)  # 빼면 다음 색인부터 다시 나온다


def test_game_folder_candidates(home, client, monkeypatch):
    for name in ("SteamLibrary", "Games/Riot Games", "사진"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr(blacklist, "drive_roots", lambda roots: [str(home)])
    found = client.get("/api/blacklist/candidates").json["folders"]
    assert found == sorted([str(home / "Games" / "Riot Games"), str(home / "SteamLibrary")])
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(home / "SteamLibrary")})
    assert client.get("/api/blacklist/candidates").json["folders"] == [str(home / "Games" / "Riot Games")]  # 차단한 것은 빠진다


def test_lock_open_close_release(home, client, monkeypatch, unlock_after):
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    secret = write(home / "비밀" / "일기.txt")
    write(home / "보통" / "메모.txt")
    build(home)
    box = str(home / "비밀")
    assert client.post("/api/lock", json={"path": box, "password": PASSWORD}).status_code == 400  # 비밀번호를 정하기 전
    assert client.post("/api/lock/password", json={"new": "12"}).status_code == 400  # 너무 짧다
    assert client.post("/api/lock/password", json={"new": PASSWORD}).status_code == 200
    secret_file = home.parent / "appdata" / "file_organizer" / "lock.dat"
    assert b"hash" not in secret_file.read_bytes() and os.stat(secret_file).st_file_attributes & 0x2  # 암호화한 숨김 파일
    assert client.post("/api/lock", json={"path": str(home), "password": PASSWORD}).status_code == 400  # 색인한 맨 위 폴더
    assert client.post("/api/lock", json={"path": box, "password": "틀림"}).status_code == 400
    assert client.post("/api/lock", json={"path": box, "password": PASSWORD}).json["locks"] == [{"path": box, "open": False, "vault": False, "missing": False, "sealed": True}]
    assert secret not in listed(client)
    with pytest.raises(PermissionError):
        os.listdir(box)
    with pytest.raises(PermissionError):
        open(secret).read()
    assert os.stat(box).st_file_attributes & 0x6 == 0x6  # 숨김·시스템
    build(home)
    assert secret not in listed(client)
    assert client.post("/api/lock/open", json={"path": box, "password": "틀림"}).status_code == 400
    assert client.post("/api/lock/open", json={"path": box, "password": PASSWORD}).json["locks"] == [{"path": box, "open": True, "vault": False, "missing": False, "sealed": None}]
    assert opened == [box] and os.listdir(box) == ["일기.txt"]
    build(home)
    assert secret not in listed(client)  # 열어 둔 동안에도 색인하지 않는다
    lock.close_all()  # 창을 닫을 때
    with pytest.raises(PermissionError):
        os.listdir(box)
    assert client.post("/api/lock/release", json={"path": box, "password": PASSWORD}).json["locks"] == []
    assert os.listdir(box) == ["일기.txt"] and os.stat(box).st_file_attributes & 0x6 == 0
    build(home)
    assert secret in listed(client)


def test_lock_and_blacklist_do_not_overlap(home, client, monkeypatch, unlock_after):
    write(home / "가" / "나" / "a.txt")
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/lock/password", json={"new": PASSWORD})
    assert client.post("/api/lock", json={"path": str(home / "가"), "password": PASSWORD}).status_code == 200
    assert client.post("/api/blacklist", json={"path": str(home / "가" / "나")}).status_code == 400
    assert client.post("/api/blacklist", json={"path": str(home)}).status_code == 400  # 잠금 폴더를 품는다
    assert client.post("/api/settings", json={"dest": str(home / "가")}).status_code == 400
    client.post("/api/lock/release", json={"path": str(home / "가"), "password": PASSWORD})


def test_folder_sizes(home, client):
    write(home / "가" / "a.txt", "x" * 1000)
    write(home / "가" / "나" / "b.txt", "y" * 500)
    write(home / "다" / "c.txt", "z" * 10)
    build(home)
    top = tree.places()[0]
    assert (top["files"], top["size"]) == (3, 1510)
    sizes = {row["name"]: (row["files"], row["size"]) for row in tree.children(str(home))}
    assert sizes == {"가": (2, 1500), "다": (1, 10)}
