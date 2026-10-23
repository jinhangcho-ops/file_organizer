"""화면 공유 대비: 앱 안의 고르기 창 목록(숨김·차단 폴더는 빼고, 새 폴더 이름 검사), 고른 폴더로 옮기기(색인 밖도 되고 차단 폴더는 거부),
잠금 창이 읽는 상태(고른 폴더 target). 지어낸 폴더만 쓴다."""
import ctypes
import os

import blacklist
import server
from conftest import build, write


def test_browse_lists_folders_and_files_but_not_hidden_or_blocked(home, client, monkeypatch):
    (home / "보임").mkdir()
    (home / "숨김").mkdir()
    ctypes.windll.kernel32.SetFileAttributesW(str(home / "숨김"), 0x2)
    (home / "막힘").mkdir()
    write(home / "모델.onnx", "x")
    write(home / "메모.txt", "x")
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(home / "막힘")})
    found = client.get(f"/api/browse?path={home}&ext=.onnx").json
    assert [f["name"] for f in found["folders"]] == ["보임"] and [f["name"] for f in found["files"]] == ["모델.onnx"]
    assert found["parent"] == str(home.parent)
    assert client.get(f"/api/browse?path={home / '막힘'}").status_code == 400  # 차단 폴더 안으로는 못 들어간다
    top = client.get("/api/browse").json
    assert top["path"] == "" and any(f["path"].endswith(":\\") for f in top["folders"])  # 맨 처음은 디스크·알려진 폴더


def test_make_folder_checks_the_name(home, client):
    assert client.post("/api/mkdir", json={"path": str(home), "name": "새 폴더"}).json["path"] == str(home / "새 폴더")
    assert os.path.isdir(home / "새 폴더")
    for bad in ("새 폴더", "a/b", "..", " "):
        assert client.post("/api/mkdir", json={"path": str(home), "name": bad}).status_code == 400, bad


def test_move_to_a_picked_folder_outside_the_index(home, client, tmp_path, monkeypatch):
    note = write(home / "메모.txt")
    outside = tmp_path / "색인 밖"
    outside.mkdir()
    build(home)
    assert client.post("/api/move", json={"paths": [note], "folder": str(outside)}).json["job"]
    assert server.jobs.wait()["result"]["moved"] == 1 and (outside / "메모.txt").exists()
    note = write(home / "메모2.txt")
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    (home / "막힘").mkdir()
    client.post("/api/blacklist", json={"path": str(home / "막힘")})
    assert client.post("/api/move", json={"paths": [note], "folder": str(home / "막힘")}).status_code == 400
    assert client.post("/api/move", json={"paths": [note]}).status_code == 400  # 고른 폴더가 없으면 거부


def test_lock_window_state_carries_the_picked_folder(home, client, monkeypatch):
    monkeypatch.setattr(server, "lock_target", str(home))
    state = client.get("/api/lock/state").json
    assert state["target"] == str(home) and state["locks"] == [] and state["password"] is False
    assert "locks" not in client.get("/api/state").json  # 본 창의 상태에는 잠금 목록을 싣지 않는다
    page = client.get("/lock")
    assert page.status_code == 200 and b"lock.js" in page.data
