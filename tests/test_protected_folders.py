"""구조 보호 폴더: 관리자만 바꾸고, 그 폴더(와 품은 위 폴더)는 옮기기·이름 바꾸기·휴지통 거부, 안의 것은 이름 바꾸기·휴지통·보관 거부.
안의 것을 밖으로 옮기기와 밖의 것을 안으로 넣기는 된다. 목록·검색에는 그대로 나온다."""
import os

import archive
import blacklist
import server
from conftest import age, build, write
from test_blocked_and_locked_folders import listed


def test_protected_folder_rules(home, client, monkeypatch, fake_trash):
    work = home / "작업"
    inner = write(work / "src" / "main.py", "print(1)")
    moving = write(work / "옮겨도 됨.txt")
    outside = write(home / "밖.txt")
    (home / "다른 곳").mkdir()
    build(home)
    assert client.post("/api/protect", json={"path": str(work)}).status_code == 400  # 관리자가 아니면 거부
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert client.post("/api/protect", json={"path": str(work)}).json["protected"] == [str(work)]
    assert client.get("/api/state").json["protected"] == [str(work)]
    assert inner in listed(client)  # 목록에는 그대로

    def refused(url, body):
        answer = client.post(url, json=body)
        return answer.status_code == 400 and "구조 보호" in answer.json["error"]

    assert refused("/api/rename", {"path": str(work), "name": "작업2"})
    assert refused("/api/move", {"paths": [str(work)], "folder": str(home / "다른 곳")})
    assert refused("/api/trash", {"paths": [str(work)], "confirm": True})
    assert refused("/api/rename", {"path": inner, "name": "app.py"})  # 안의 것
    assert refused("/api/trash", {"paths": [inner], "confirm": True})
    assert fake_trash == [] and os.path.exists(inner)
    assert client.post("/api/move", json={"paths": [moving], "folder": str(home / "다른 곳")}).json["job"]  # 밖으로는 된다
    assert server.jobs.wait()["result"]["moved"] == 1
    assert client.post("/api/move", json={"paths": [outside], "folder": str(work)}).json["job"]  # 안으로도 된다
    assert server.jobs.wait()["result"]["moved"] == 1 and os.path.exists(work / "밖.txt")
    age(inner, 800)
    build(home)
    assert inner not in {row["path"] for row in archive.candidates(365)}  # 보관하면 원본이 휴지통으로 가므로 뺀다
    assert client.post("/api/protect", json={"path": str(work), "remove": True}).json["protected"] == []
    assert inner in {row["path"] for row in archive.candidates(365)}  # 보호를 풀면 보관 대상(위 확인이 헛돌지 않았다)
    assert client.post("/api/rename", json={"path": inner, "name": "app.py"}).status_code == 200


def test_parent_of_protected_folder_and_blocked_overlap(home, client, monkeypatch, fake_trash):
    write(home / "위" / "작업" / "a.txt")
    write(home / "막힘" / "b.txt")
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/protect", json={"path": str(home / "위" / "작업")})
    parent = str(home / "위")
    assert client.post("/api/trash", json={"paths": [parent], "confirm": True}).status_code == 400
    assert client.post("/api/rename", json={"path": parent, "name": "위2"}).status_code == 400
    client.post("/api/blacklist", json={"path": str(home / "막힘")})
    assert client.post("/api/protect", json={"path": str(home / "막힘")}).status_code == 400  # 차단 폴더 안은 이미 다루지 않는다
    assert client.post("/api/protect", json={"path": str(home / "위" / "작업" / "없음")}).status_code == 400  # 없는 폴더
