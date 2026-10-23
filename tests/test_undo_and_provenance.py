"""Ctrl+Z 되돌리기(옮기기·이름 바꾸기·다운로드 정리, 최근 20개)와 상세의 받은 곳·편집한 사람. 휴지통은 쓰지 않고 파일은 지어낸다."""
import getpass
import os

import blacklist
import server
from conftest import build, write


def undo(client):
    answer = client.post("/api/undo", json={})
    if answer.status_code != 200:
        return answer
    server.jobs.wait()
    return server.jobs.status


def test_undo_move_and_rename_in_reverse_order(home, client):
    note = write(home / "가" / "메모.txt", "x")
    (home / "나").mkdir()
    build(home)
    assert client.post("/api/move", json={"paths": [note], "folder": str(home / "나")}).json["job"]
    server.jobs.wait()
    moved = str(home / "나" / "메모.txt")
    renamed = client.post("/api/rename", json={"path": moved, "name": "새 이름.txt"}).json["path"]
    status = undo(client)  # 이름 바꾸기부터 거꾸로
    assert status["result"]["moved"] == 1 and os.path.exists(moved) and not os.path.exists(renamed)
    assert undo(client)["result"]["moved"] == 1 and os.path.exists(note) and not os.path.exists(moved)
    answer = client.post("/api/undo", json={})
    assert answer.status_code == 400 and "되돌릴 것이 없" in answer.json["error"]
    assert [r["name"] for r in client.post("/api/files", json={"folder": str(home / "가")}).json["rows"]] == ["메모.txt"]  # 색인도 따라옴


def test_undo_keeps_the_file_when_its_place_is_taken_or_blocked(home, client, monkeypatch):
    note = write(home / "가" / "메모.txt", "x")
    (home / "나").mkdir()
    build(home)
    client.post("/api/move", json={"paths": [note], "folder": str(home / "나")})
    server.jobs.wait()
    write(home / "가" / "메모.txt", "새로 생긴 같은 이름")
    status = undo(client)
    assert status["result"]["moved"] == 0 and "같은 이름" in status["result"]["failed"][0]["error"]
    assert open(home / "가" / "메모.txt", encoding="utf-8").read() == "새로 생긴 같은 이름"  # 덮어쓰지 않는다
    other = write(home / "다" / "b.txt")
    (home / "라").mkdir()
    build(home)
    client.post("/api/move", json={"paths": [other], "folder": str(home / "라")})
    server.jobs.wait()
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(home / "다")})
    status = undo(client)
    assert status["result"]["moved"] == 0 and os.path.exists(home / "라" / "b.txt")  # 그사이 차단한 곳으로는 되돌리지 않는다


def test_undo_organize_and_only_the_last_20(home, client):
    source, dest = home / "받은 곳", home / "정리"
    write(source / "보고서.txt")
    dest.mkdir()
    build(home)
    client.post("/api/organize/run", json={"source": str(source), "dest": str(dest)})
    server.jobs.wait()
    assert (dest / "문서" / "보고서.txt").exists()
    assert undo(client)["result"]["moved"] == 1 and (source / "보고서.txt").exists()
    names = [write(home / "많이" / f"{n:02}.txt") for n in range(22)]
    build(home)
    for n, path in enumerate(names):
        client.post("/api/rename", json={"path": path, "name": f"바뀜{n:02}.txt"})
    for _ in range(20):
        assert undo(client)["result"]["moved"] == 1
    assert client.post("/api/undo", json={}).status_code == 400  # 21번째부터는 기억하지 않는다
    assert sorted(os.listdir(home / "많이"))[:2] == ["02.txt", "03.txt"] and "바뀜00.txt" in os.listdir(home / "많이")


def test_provenance_of_downloaded_and_office_files(home, client):
    from docx import Document

    doc = Document()
    doc.add_paragraph("내용")
    doc.core_properties.author = "김작성"
    doc.core_properties.last_modified_by = "이수정"
    path = str(home / "받은 문서.docx")
    doc.save(path)
    with open(path + ":Zone.Identifier", "w", encoding="utf-8") as stream:
        stream.write("[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://example.com/page?ref=abc\n"
                     "HostUrl=https://user:pw@files.example.com/d/report.docx?token=SECRET\n")
    build(home)
    info = client.get("/api/provenance?path=" + path).json
    assert info["source"] == "https://files.example.com/d/report.docx?…" and info["referrer"] == "https://example.com/page?…"
    assert "SECRET" not in str(info) and "pw" not in info["source"]
    assert info["author"] == "김작성" and info["last_saved_by"] == "이수정"
    assert info["owner"].lower().endswith(getpass.getuser().lower())
    plain = write(home / "그냥.txt")
    build(home)
    info = client.get("/api/provenance?path=" + plain).json
    assert info["source"] == "" and info["author"] == "" and info["owner"]


def test_pdf_author(home, client):
    body = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
            b"4 0 obj<</Author(Park Writer)/Title(t)>>endobj\ntrailer<</Root 1 0 R/Info 4 0 R/Size 5>>\n%%EOF\n")
    (home / "a.pdf").write_bytes(body)
    build(home)
    assert client.get("/api/provenance?path=" + str(home / "a.pdf")).json["author"] == "Park Writer"
    assert client.get("/api/provenance?path=" + str(home)).status_code == 400  # 폴더는 아니다
