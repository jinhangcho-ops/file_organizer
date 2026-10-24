"""보호 장치의 빈틈과 색인이 멈추지 않고 이어지는지: Windows 폴더를 품은 폴더, 지운 차단 폴더 자리, 색인 밖으로 옮겼다 되돌리기,
정션 자체 옮기기·지우기, subst 드라이브, 아주 큰 사진·깨진 문서, 지문을 다시 만들 때 인식 결과, 짝 맞추기 도중 끊김,
색인 도중 이름을 바꾼 폴더의 글자. 파일은 모두 지어낸다."""
import os
import subprocess
import types
import zipfile

import numpy
import pytest
from PIL import Image

import blacklist
import index
import media
import organize
import recognize
import server
import trash
from conftest import build, write


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_folder_holding_windows_folders_is_not_moved_or_trashed(home, client, monkeypatch, fake_trash):
    profile = home / "사용자"
    write(profile / "AppData" / "Local" / "설정.txt")
    write(profile / "문서" / "a.txt")
    build(home)
    monkeypatch.setenv("LOCALAPPDATA", str(profile / "AppData" / "Local"))  # 이 폴더를 사용자 폴더로 흉내 낸다
    answer = client.post("/api/trash", json={"paths": [str(profile)], "confirm": True})
    assert answer.status_code == 400 and "Windows" in answer.json["error"]
    assert client.post("/api/move", json={"paths": [str(profile)], "folder": str(home)}).status_code == 400
    assert client.post("/api/folder/empty", json={"path": str(home), "confirm": False}).json["refused"]
    assert fake_trash == []


def test_move_does_not_land_in_a_deleted_blocked_folder(home, client, monkeypatch):
    old = write(home / "X" / "Old" / "a.txt")
    (home / "D" / "Old").mkdir(parents=True)
    build(home)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    blacklist.add(str(home / "D" / "Old"), set())
    os.rmdir(home / "D" / "Old")  # 탐색기에서 지웠다(차단 목록에는 남아 있다)
    client.post("/api/move", json={"paths": [str(home / "X" / "Old")], "folder": str(home / "D")})
    server.jobs.wait()
    result = server.jobs.status["result"]
    assert result["moved"] == 0 and "차단" in result["failed"][0]["error"] and os.path.exists(old)


def test_folder_moved_out_of_the_index_comes_back_on_undo(home, client, tmp_path):
    write(home / "가" / "a.txt")
    write(home / "가" / "안" / "b.txt")
    build(home)
    outside = tmp_path / "밖"
    outside.mkdir()
    client.post("/api/move", json={"paths": [str(home / "가")], "folder": str(outside)})
    server.jobs.wait()
    client.post("/api/undo", json={})
    server.jobs.wait()
    names = sorted(r["name"] for r in client.post("/api/files", json={"folder": str(home / "가")}).json["rows"])
    assert names == ["a.txt", "b.txt"]


def test_junction_itself_is_not_copied_or_judged_by_its_target(tmp_path, monkeypatch):
    target = tmp_path / "가리키는 곳"
    write(target / "남의 파일.txt", "그대로")
    link = tmp_path / "링크"
    junction(link, target)
    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)
    with pytest.raises(OSError, match="정션"):
        organize.move_folder(str(link), str(tmp_path / "다른 곳"))
    assert (target / "남의 파일.txt").exists()
    seen = []
    monkeypatch.setattr(trash.drives, "drive_type", lambda path: seen.append(path) or 3)
    trash.problem(str(link))
    assert seen == [str(tmp_path)]  # 지워지는 것은 링크: 링크가 있는 곳의 디스크로 본다
    os.rmdir(link)


def test_subst_drive_is_refused():
    assert trash.drives.is_subst("C:\\") is False


def test_subst_drive_refused_in_problem(tmp_path, monkeypatch):
    path = write(tmp_path / "a.txt")
    monkeypatch.setattr(trash.drives, "is_subst", lambda path: True)
    assert "확인할 수 없는" in trash.problem(path)


def test_huge_picture_and_broken_document_do_not_stop_the_steps(tmp_path, monkeypatch):
    picture = tmp_path / "큰 그림.png"
    Image.new("L", (400, 300)).save(picture)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # 400×300이 '아주 큰 사진'이 되게
    assert media.image_hash(str(picture)) is None
    stub = types.SimpleNamespace(np=numpy, custom=None, faces=False, kinds=lambda v: [], image_vector=lambda image: None)
    row = {"id": 1, "path": str(picture), "stamp": os.stat(picture).st_mtime_ns, "ext": ".png"}
    db = index.connect()
    try:
        recognize.recognize_one(db, stub, row, {"video_interval": 30}, None)  # 멈추지 않고 빈 결과로 표시
        assert db.execute("select count(*) from recognized where id = 1").fetchone()[0] == 1
    finally:
        db.close()
    doc = tmp_path / "깨진 문서.docx"
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "".join(f"<p>문장 {n}번 내용이 여기에 있습니다</p>" for n in range(2000)))
    data = bytearray(doc.read_bytes())
    for i in range(200, 400):
        data[i] ^= 0x5A
    doc.write_bytes(bytes(data))
    assert index.doc_fingerprint(str(doc)) == ""


def test_refingerprint_keeps_recognition_and_interrupted_matching_resumes(home, monkeypatch):
    picture = home / "그림.png"
    img = Image.new("RGB", (64, 48), (200, 30, 30))
    img.paste((20, 40, 220), (0, 0, 20, 48))  # 한 가지 색이면 지문이 없으므로 무늬를 넣는다
    img.paste((240, 240, 40), (40, 10, 64, 30))
    img.save(picture)
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = '그림.png'").fetchone())
        db.execute("insert into tags(id, kind, label, score) values(?, 'group', '사진', 90)", (row["id"],))
        media.fingerprint(db, row, 30)
        assert db.execute("select count(*) from tags where id = ?", (row["id"],)).fetchone()[0] == 1  # 인식 결과는 그대로
        assert db.execute("select count(*) from media_todo where id = ?", (row["id"],)).fetchone()[0] == 1

        def cut(*args):
            raise RuntimeError("끊김")

        monkeypatch.setattr(media, "pair", cut)
        db.execute("insert into media(id, kind, frames) values(999, 'image', ?)",
                   (db.execute("select frames from media where id = ?", (row["id"],)).fetchone()[0],))  # 똑같은 사진 하나
        with pytest.raises(RuntimeError):
            media.match_images(db)
        assert db.execute("select count(*) from media_todo where id = ?", (row["id"],)).fetchone()[0] == 1  # 짝을 넣기 전에는 할 일에 남는다
    finally:
        db.close()


def test_text_survives_a_folder_renamed_during_indexing(home, monkeypatch):
    write(home / "자료" / "보고서.txt", "분기 실적과 다음 계획을 정리한 보고서입니다. " * 5)
    build(home)
    real_scan, real_walk = index.scan, index.walk

    def renamed_meanwhile(db, *args):
        os.rename(home / "자료", home / "자료2")  # 색인하는 동안 앱에서 이름을 바꿨다
        index.move_folder_rows(db, str(home / "자료"), str(home / "자료2"))
        return real_scan(db, *args)

    def walk_before_rename(*args, **kwargs):  # 훑기는 이미 옛 이름을 쌓아 두어 새 이름의 폴더를 보지 못한다
        return ((folder, entries) for folder, entries in real_walk(*args, **kwargs) if "자료2" not in folder)

    monkeypatch.setattr(index, "scan", renamed_meanwhile)
    monkeypatch.setattr(index, "walk", walk_before_rename)
    build(home)
    db = index.connect()
    try:
        file_id = db.execute("select id from files where name = '보고서.txt'").fetchone()[0]
        assert db.execute("select count(*) from body where rowid = ?", (file_id,)).fetchone()[0] == 1
    finally:
        db.close()
