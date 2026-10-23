"""탐색기처럼 폴더 보기(바로 든 파일만), 폴더 설명(요약·메모), 폴더 비우기. 휴지통은 가짜(fake_trash), 파일은 지어낸다."""
import ctypes
import os

import blacklist
import notes
import server
from conftest import build, write


def test_direct_files_only(home, client):
    write(home / "가" / "a.txt")
    write(home / "가" / "안" / "b.txt")
    build(home)
    names = lambda **q: sorted(r["name"] for r in client.post("/api/files", json={"folder": str(home / "가")} | q).json["rows"])  # noqa: E731
    assert names(direct=True) == ["a.txt"] and names() == ["a.txt", "b.txt"]


def test_folder_summary_and_note_follow_moves(home, client, fake_trash):
    write(home / "가" / "보고서.docx", "x" * 300)
    write(home / "가" / "사진.jpg", "x" * 100)
    write(home / "가" / "안" / "깊이" / "메모.txt", "y" * 50)
    build(home)
    s = client.get(f"/api/folder_summary?path={home / '가'}").json
    summary = s["summary"]
    assert (summary["files"], summary["size"], summary["children"], summary["below"]) == (3, 450, 1, 2) and s["note"] == ""
    assert [b["name"] for b in summary["biggest"]] == ["보고서.docx", "사진.jpg", "메모.txt"] and dict(summary["kinds"])["문서"] == 2
    assert client.post("/api/note", json={"path": str(home / "가" / "안"), "text": "  계약 자료  "}).json["note"] == "계약 자료"
    assert client.post("/api/note", json={"path": str(home / "가" / "안"), "text": "x" * 501}).status_code == 400
    renamed = client.post("/api/rename", json={"path": str(home / "가"), "name": "나"}).json["path"]
    assert notes.get(os.path.join(renamed, "안")) == "계약 자료" and notes.get(str(home / "가" / "안")) == ""  # 위 폴더 이름 바꾸기를 따라감
    assert client.post("/api/trash", json={"paths": [renamed], "confirm": True}).status_code == 200
    server.jobs.wait()
    assert notes.load() == {}  # 휴지통으로 보내면 메모도 지운다


def test_empty_folder(home, client, fake_trash, monkeypatch):
    box = home / "비울 곳"
    write(box / "a.txt")
    write(box / "안" / "b.txt")
    hidden = write(box / "desktop.ini")
    ctypes.windll.kernel32.SetFileAttributesW(hidden, 0x2 | 0x4)
    write(box / "막힘" / "c.txt")
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(box / "막힘")})
    plan = client.post("/api/folder/empty", json={"path": str(box)}).json
    assert plan["count"] == 2 and plan["hidden"] == 1 and [os.path.basename(r["from"]) for r in plan["refused"]] == ["막힘"]
    assert fake_trash == []  # 확인 전에는 보내지 않는다
    job = client.post("/api/folder/empty", json={"path": str(box), "confirm": True}).json["job"]
    result = server.jobs.wait()["result"]
    assert job and result["trashed"] == 2 and result["hidden"] == 1
    assert sorted(os.listdir(box)) == ["desktop.ini", "막힘"]  # 폴더 자신·숨김 파일·차단 폴더는 남는다
    client.post("/api/protect", json={"path": str(box)})
    answer = client.post("/api/folder/empty", json={"path": str(box)})
    assert answer.status_code == 400 and "구조 보호" in answer.json["error"]
