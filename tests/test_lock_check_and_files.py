"""잠금 창의 실제 잠금 확인(sealed)과 잠금 파일 보기(열어 둔 잠금 폴더 안만 보이고 연다).
잠금은 임시 폴더에서 실제 icacls로 건다(끝나면 unlock_after가 모두 푼다). 비밀번호는 지어낸 값이다."""
import os
import subprocess

import pytest

import lock
from conftest import build, write
from test_blocked_and_locked_folders import PASSWORD


@pytest.fixture
def box(home, client, monkeypatch, unlock_after):
    """잠근 폴더 '비밀'(안에 일기.txt와 하위 폴더 사진\\1.jpg). os.startfile은 연 경로만 적는다."""
    write(home / "비밀" / "일기.txt", "내용")
    write(home / "비밀" / "사진" / "1.jpg", "x")
    build(home)
    client.post("/api/lock/password", json={"new": PASSWORD})
    path = str(home / "비밀")
    assert client.post("/api/lock", json={"path": path, "password": PASSWORD}).status_code == 200
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    return path, opened


def sealed(client):
    return [item["sealed"] for item in client.get("/api/lock/state").json["locks"]]


def test_check_finds_locks_undone_outside_the_app(box, client):
    path, _ = box
    assert sealed(client) == [True]
    subprocess.run(["icacls", path, "/remove:d", f"*{lock.user_sid()}"], capture_output=True, check=True)  # 앱 밖에서 권한만 뗐다
    assert sealed(client) == [False]  # 기록은 잠김이지만 실제로는 열린다
    assert client.post("/api/lock/close", json={"path": path}).status_code == 200  # [다시 잠그기]
    assert sealed(client) == [True]
    with pytest.raises(PermissionError):
        os.listdir(path)
    lock.unseal(path)  # 권한도 숨김도 풀렸다
    assert sealed(client) == [False]


def test_open_folder_has_no_check_and_missing_is_none(box, client):
    path, _ = box
    client.post("/api/lock/open", json={"path": path, "password": PASSWORD, "explorer": False})
    assert sealed(client) == [None]  # 열어 둔 것은 잠겨 있지 않은 것이 맞다


def test_files_only_inside_opened_lock(box, client, home):
    path, opened = box
    top = client.get("/api/lock/files").json
    assert top["folders"] == [{"path": "", "label": path, "open": False}]
    assert client.get("/api/lock/files", query_string={"path": path}).status_code == 400  # 잠긴 동안은 안 보인다
    answer = client.post("/api/lock/open", json={"path": path, "password": PASSWORD, "explorer": False}).json
    assert answer["path"] == path and opened == []  # 잠금 파일 보기에서 열면 탐색기는 띄우지 않는다
    inside = client.get("/api/lock/files", query_string={"path": path}).json
    assert [f["name"] for f in inside["folders"]] == ["사진"] and [f["name"] for f in inside["files"]] == ["일기.txt"]
    assert inside["parent"] == "" and inside["label"] == path  # 맨 위에서 [위로]는 잠금 폴더들로
    deeper = client.get("/api/lock/files", query_string={"path": os.path.join(path, "사진")}).json
    assert deeper["parent"] == path and [f["name"] for f in deeper["files"]] == ["1.jpg"]
    write(home / "밖.txt")
    for outside in (str(home), str(home / "밖.txt"), os.path.join(path, "..")):  # 잠금 폴더 밖(.. 로 올라가기 포함)
        assert client.get("/api/lock/files", query_string={"path": outside}).status_code == 400
        assert client.post("/api/lock/open_file", json={"path": outside}).status_code == 400
    assert client.post("/api/lock/open_file", json={"path": os.path.join(path, "일기.txt")}).status_code == 200
    assert opened == [os.path.join(path, "일기.txt")]
    client.post("/api/lock/close", json={"path": path})
    assert client.post("/api/lock/open_file", json={"path": os.path.join(path, "일기.txt")}).status_code == 400  # 다시 잠근 뒤
    assert opened == [os.path.join(path, "일기.txt")]


def test_files_skip_junctions_out_of_the_lock(box, client, home):
    path, _ = box
    client.post("/api/lock/open", json={"path": path, "password": PASSWORD, "explorer": False})
    (home / "다른 곳").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(path, "링크"), str(home / "다른 곳")], capture_output=True, check=True)
    names = [f["name"] for f in client.get("/api/lock/files", query_string={"path": path}).json["folders"]]
    assert names == ["사진"]  # 잠금 폴더 밖을 가리키는 정션은 보이지 않는다
    os.rmdir(os.path.join(path, "링크"))


def test_vault_files_show_original_place(home, client, monkeypatch, unlock_after):
    monkeypatch.setattr(os, "startfile", lambda path: None)
    write(home / "여행" / "1.jpg", "x")
    build(home)
    client.post("/api/lock/password", json={"new": PASSWORD})
    place = home.parent / "금고 둘 곳"
    place.mkdir()
    client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)})
    trip = str(home / "여행")
    client.post("/api/lock", json={"path": trip, "password": PASSWORD, "vault": True})
    assert sealed(client) == [True]
    assert client.get("/api/lock/files").json["folders"] == [{"path": "", "label": trip, "open": False}]
    inside = client.post("/api/lock/open", json={"path": trip, "password": PASSWORD, "explorer": False}).json["path"]
    assert inside.startswith(str(place / ".vault"))  # 실제로는 금고 안 무작위 이름
    seen = client.get("/api/lock/files", query_string={"path": inside}).json
    assert seen["label"] == trip and [f["name"] for f in seen["files"]] == ["1.jpg"]  # 보이는 이름은 원래 위치
    client.post("/api/lock/close", json={"path": trip})
    assert sealed(client) == [True]
