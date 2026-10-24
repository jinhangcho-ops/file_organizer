"""옮기기·휴지통의 드문 경우: 맨 위 폴더 이름 바꾸기, 지금 자리가 차단된 되돌리기, 다른 디스크로 옮길 때의 정션·남의 파일,
깨진 정션의 크기, 확인할 수 없는 디스크, 색인에 남은 지난 기록 자리로 옮기기, 차단 폴더 안으로 정리, 동영상 짝 다시 맞추기. 파일은 지어낸다."""
import os
import subprocess

import pytest

import blacklist
import index
import kinds
import media
import organize
import server
import settings
import trash
from conftest import build, write


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_rename_refuses_indexed_root(home, client):
    write(home / "a.txt")
    build(home)
    answer = client.post("/api/rename", json={"path": str(home), "name": "다른 이름"})
    assert answer.status_code == 400 and "맨 위 폴더" in answer.json["error"] and home.is_dir()


def test_undo_refuses_when_current_place_is_blocked(home, client, monkeypatch):
    note = write(home / "가" / "b.txt")
    (home / "라").mkdir()
    build(home)
    client.post("/api/move", json={"paths": [note], "folder": str(home / "라")})
    server.jobs.wait()
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    assert client.post("/api/blacklist", json={"path": str(home / "라")}).status_code == 200
    client.post("/api/undo", json={})
    server.jobs.wait()
    assert server.jobs.status["result"]["moved"] == 0 and (home / "라" / "b.txt").exists() and not os.path.exists(note)


def test_other_drive_folder_move_refuses_junction_and_existing_target(tmp_path, monkeypatch):
    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)  # 복사해서 옮기는 길을 같은 디스크에서 본다
    elsewhere = tmp_path / "다른 곳"
    write(elsewhere / "남의 파일.txt", "그대로")
    source = tmp_path / "옮길 폴더"
    write(source / "a.txt")
    junction(source / "링크", elsewhere)
    with pytest.raises(OSError, match="정션"):
        organize.move_folder(str(source), str(tmp_path / "옮긴 곳"))
    assert (elsewhere / "남의 파일.txt").read_text(encoding="utf-8") == "그대로" and (source / "a.txt").exists()
    assert not (tmp_path / "옮긴 곳").exists()
    os.rmdir(source / "링크")
    write(tmp_path / "있던 곳" / "있던 파일.txt", "남의 것")
    with pytest.raises(OSError):
        organize.move_folder(str(source), str(tmp_path / "있던 곳"))  # 이미 있는 폴더에 섞지 않는다
    assert (tmp_path / "있던 곳" / "있던 파일.txt").read_text(encoding="utf-8") == "남의 것"


def test_other_drive_file_move_keeps_a_file_that_appeared(tmp_path, monkeypatch):
    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)
    before = write(tmp_path / "a.txt", "원본")
    after = write(tmp_path / "옮길 곳" / "a.txt", "그사이 생긴 남의 파일")
    with pytest.raises(OSError):
        organize.move(before, after)
    assert open(after, encoding="utf-8").read() == "그사이 생긴 남의 파일" and os.path.exists(before)


def test_trash_size_skips_broken_junction_and_refuses_unknown_volume(tmp_path, monkeypatch):
    folder = tmp_path / "폴더"
    write(folder / "a.txt", "12345")
    gone = tmp_path / "사라질 곳"
    write(gone / "큰 파일.txt", "x" * 1000)
    junction(folder / "링크", gone)
    assert trash.size_of(str(folder)) == 5  # 정션이 가리키는 곳은 세지 않는다
    for name in os.listdir(gone):
        os.remove(gone / name)
    os.rmdir(gone)
    assert trash.size_of(str(folder)) == 5 and trash.problem(str(folder)) is None  # 깨진 정션에서 멈추지 않는다
    os.rmdir(folder / "링크")
    monkeypatch.setattr(trash, "volume_id", lambda path: None)  # subst 드라이브처럼 볼륨을 모를 때
    assert "확인할 수 없는" in trash.problem(str(folder))


def test_move_onto_a_stale_index_row(home, client):
    write(home / "가" / "초안.txt")
    stale = write(home / "가" / "보고서.txt")
    build(home)
    os.remove(stale)  # 탐색기에서 지웠고 색인에는 아직 남아 있다
    answer = client.post("/api/rename", json={"path": str(home / "가" / "초안.txt"), "name": "보고서.txt"})
    assert answer.status_code == 200
    db = index.connect()
    try:
        assert [r[0] for r in db.execute("select name from files")] == ["보고서.txt"]
    finally:
        db.close()
    assert client.post("/api/undo", json={}).status_code == 200  # 되돌리기 기록도 남았다
    server.jobs.wait()
    assert (home / "가" / "초안.txt").exists()


def test_organize_does_not_move_into_a_blocked_folder(home, tmp_path, monkeypatch):
    source, dest = tmp_path / "받은 곳", tmp_path / "놓을 곳"
    write(source / "메모.txt")
    write(source / "사진.jpg")
    (dest / kinds.of(".txt")).mkdir(parents=True)
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    blacklist.add(str(dest / kinds.of(".txt")), set())
    assert [os.path.basename(m["from"]) for m in organize.plan(str(source), str(dest))] == ["사진.jpg"]


def test_video_pairs_are_matched_again_only_when_needed(home, monkeypatch):
    calls = []
    monkeypatch.setattr(media, "match_videos", lambda db, similar: calls.append(similar))
    db = index.connect()
    try:
        media.run(db)
        media.run(db)  # 새 동영상도, 바뀐 기준도 없다
        settings.save({"video_similar": settings.load()["video_similar"] + 1})
        media.run(db)
    finally:
        db.close()
    assert len(calls) == 2
