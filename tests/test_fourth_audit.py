"""네 번째 감사에서 막은 것: 260자 넘는 경로의 휴지통(그대로 지워졌다), 폴더에 탑재한 디스크로 옮기기 검사, 구조 보호 폴더 안의
이름 되돌리기, 제자리 잠금의 범위, 끊긴 HEIC, 게임 묶음 폴더·정리의 깨진 이름, 뽑은 디스크가 든 색인 목록 저장, 인식 모델 오류,
지운 안쪽 색인 폴더, 깨진 문서의 받은 곳, 멈춘 동영상 짝 맞추기. 파일은 모두 지어낸다."""
import os
import types
import zipfile

import numpy
import pytest
from PIL import Image

import blacklist
import index
import media
import organize
import provenance
import recognize
import server
import settings
import trash
from conftest import build, write


def test_long_paths_are_not_sent_to_the_trash(tmp_path):
    folder = tmp_path / "프로젝트"
    deep = folder / ("가" * 60) / ("나" * 60) / ("다" * 60)
    target = deep / (("라" * (270 - len(str(deep)) - 5)) + ".txt")
    write(target)
    assert len(str(target)) >= 260
    assert "너무 길어" in trash.problem(str(folder)) and "너무 길어" in trash.problem(str(target))
    write(tmp_path / "짧은.txt")
    assert trash.problem(str(tmp_path / "짧은.txt")) is None


def test_move_asks_the_drive_of_the_place_it_moves_to(home, client, monkeypatch):
    write(home / "가" / "a.txt")
    (home / "나").mkdir()
    build(home)
    asked = []
    monkeypatch.setattr(server.drives, "same_drive", lambda a, b: asked.append(b) or True)
    client.post("/api/move", json={"paths": [str(home / "가")], "folder": str(home / "나")})
    server.jobs.wait()
    assert asked[0] == str(home / "나" / "가")  # 옮길 곳 폴더 자신이 아니라 옮겨 갈 경로(폴더에 탑재한 디스크 때문)


def test_undoing_a_rename_respects_structure_protection(home, client, monkeypatch):
    write(home / "작업" / "a.txt")
    build(home)
    client.post("/api/rename", json={"path": str(home / "작업" / "a.txt"), "name": "b.txt"})
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert client.post("/api/protect", json={"path": str(home / "작업")}).status_code == 200
    client.post("/api/undo", json={})
    server.jobs.wait()
    assert server.jobs.status["result"]["moved"] == 0 and (home / "작업" / "b.txt").exists()


def test_lock_in_place_allows_protected_and_refuses_windows(home, client, monkeypatch, unlock_after):
    write(home / "보호" / "a.txt")
    write(home / "사용자" / "AppData" / "Local" / "설정.txt")
    write(home / "게임" / "UnityPlayer.dll")
    write(home / "게임" / "게임.exe")
    build(home)
    client.post("/api/lock/password", json={"new": "check-5678"})  # 지어낸 시험용 비밀번호
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/protect", json={"path": str(home / "보호")})
    monkeypatch.setenv("LOCALAPPDATA", str(home / "사용자" / "AppData" / "Local"))
    answer = client.post("/api/lock", json={"path": str(home / "사용자"), "password": "check-5678"})
    assert answer.status_code == 400 and "잠글 수 없습니다" in answer.json["error"]
    answer = client.post("/api/lock", json={"path": str(home / "게임"), "password": "check-5678"})  # 그 자신이 게임 폴더(refuse_system)
    assert answer.status_code == 400 and "게임 폴더라" in answer.json["error"], answer.json
    assert client.post("/api/lock", json={"path": str(home / "보호"), "password": "check-5678"}).status_code == 200  # 제자리 잠금은 구조를 바꾸지 않는다


def test_broken_heic_and_bad_names_do_not_stop_the_index(home, tmp_path, monkeypatch):
    picture = write(tmp_path / "끊긴.heic", "x")

    def cut(*args, **kwargs):
        raise EOFError("Unexpected end of file")

    with monkeypatch.context() as patch:  # undo()는 home의 임시 설정 폴더까지 되돌리므로 이 바꿔 끼우기만 따로
        patch.setattr(Image, "open", cut)
        assert media.image_hash(picture) is None
    (home / "Epic Games" / "bad\udc80game").mkdir(parents=True)  # 게임 묶음 폴더 안의 깨진 이름
    write(home / "a.txt")
    build(home)
    downloads = tmp_path / "받은 곳"
    write(downloads / "보통.txt")
    write(downloads / "bad\udc80.txt")
    (tmp_path / "놓을 곳").mkdir()
    assert [os.path.basename(m["from"]) for m in organize.plan(str(downloads), str(tmp_path / "놓을 곳"))] == ["보통.txt"]


def test_roots_with_an_unplugged_drive_can_still_be_saved(home, tmp_path):
    disk = tmp_path / "외장"
    disk.mkdir()
    settings.save({"roots": [str(home), str(disk)]})
    os.rename(disk, tmp_path / "뽑음")
    other = tmp_path / "새 폴더"
    other.mkdir()
    assert settings.save({"roots": [str(home), str(disk), str(other)]})["roots"] == [str(home), str(disk), str(other)]
    with pytest.raises(ValueError):
        settings.save({"roots": [str(home), str(tmp_path / "처음 보는 없는 폴더")]})


def test_model_errors_stop_recognition_but_file_errors_do_not(tmp_path):
    picture = tmp_path / "그림.png"
    Image.new("RGB", (40, 30), (10, 200, 10)).save(picture)

    def model_fails(image):
        raise RuntimeError("맞지 않는 모델")

    stub = types.SimpleNamespace(np=numpy, custom=None, faces=False, kinds=lambda v: [], image_vector=model_fails)
    row = {"id": 7, "path": str(picture), "stamp": os.stat(picture).st_mtime_ns, "ext": ".png"}
    db = index.connect()
    try:
        with pytest.raises(RuntimeError):
            recognize.recognize_one(db, stub, row, {"video_interval": 30}, None)
        assert db.execute("select count(*) from recognized where id = 7").fetchone()[0] == 0  # '인식함'으로 굳지 않는다
    finally:
        db.close()


def test_deleted_inner_root_is_forgotten(home):
    inner = home / "Projects"
    write(inner / "a.txt")
    settings.save({"roots": [str(home), str(inner)]})
    build(home)
    os.remove(inner / "a.txt")
    os.rmdir(inner)  # 위 색인 폴더를 끝까지 훑었으니 지운 것을 안다
    build(home)
    db = index.connect()
    try:
        assert db.execute("select count(*) from files").fetchone()[0] == 0
    finally:
        db.close()


def test_broken_document_has_no_people_and_stopped_video_matching_resumes(tmp_path):
    doc = tmp_path / "깨진.docx"
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("docProps/core.xml", "<cp:coreProperties>" + os.urandom(3000).hex() + "</cp:coreProperties>")  # 잘 줄지 않는 내용
    data = bytearray(doc.read_bytes())
    for i in range(80, 300):
        data[i] ^= 0x5A
    doc.write_bytes(bytes(data))
    assert provenance.office_people(str(doc)) == ("", "")
    db = index.connect()
    try:
        db.execute("delete from meta")
        index.stop.set()
        media.run(db)
        assert db.execute("select count(*) from meta where name = 'video_similar'").fetchone()[0] == 0  # 멈추면 다음에 다시 맞춘다
    finally:
        index.stop.clear()
        db.close()
