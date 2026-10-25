"""세 번째 감사에서 막은 것: 제자리 잠금의 Windows·게임 폴더, 다른 계정의 사용자 폴더, 읽기 전용 파일,
되돌리기가 지운 차단 폴더 자리로 들어가기, 지운 위 폴더로 되돌리기, 이름이 깨진 파일, 깨진 EXIF, 뽑은 외장 디스크,
휴지통 정책·subst 판별, 끝난 작업의 결과. 파일은 모두 지어낸다."""
import os
import stat
import time

from PIL import Image, ImageOps

import blacklist
import index
import jobs
import media
import organize
import server
import settings
import trash
from conftest import build, write


def test_lock_refuses_windows_and_game_folders(home, client, monkeypatch, unlock_after):
    write(home / "사용자" / "AppData" / "Local" / "설정.txt")
    write(home / "게임" / "UnityPlayer.dll")
    write(home / "게임" / "게임.exe")
    build(home)
    client.post("/api/lock/password", json={"new": "check-5678"})  # 지어낸 시험용 비밀번호
    monkeypatch.setenv("LOCALAPPDATA", str(home / "사용자" / "AppData" / "Local"))
    for path, why in ((home / "사용자", "Windows"), (home / "게임", "게임")):
        answer = client.post("/api/lock", json={"path": str(path), "password": "check-5678"})
        assert answer.status_code == 400 and why in answer.json["error"], answer.json


def test_other_accounts_profile_is_not_trashed(home, client, monkeypatch, fake_trash):
    other = home / "다른 계정"
    write(other / "AppData" / "Roaming" / "a.txt")
    build(home)
    monkeypatch.setattr(trash.drives, "profile_folders", lambda: [str(other)])
    assert client.post("/api/trash", json={"paths": [str(other)], "confirm": True}).status_code == 400 and fake_trash == []


def test_read_only_file_moves_whole_to_another_drive(tmp_path, monkeypatch):
    """7차에서 바꿈: 읽기 전용 파일도 다른 디스크로 옮긴다(같은 디스크에서는 원래 옮겨졌다). 같은 파일이 두 곳에 남지 않는다."""
    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)
    source = tmp_path / "프로젝트"
    write(source / "a.txt")
    locked = write(source / ".git" / "obj", "읽기 전용")
    os.chmod(locked, stat.S_IREAD)
    try:
        organize.move_folder(str(source), str(tmp_path / "옮긴 곳"))
        assert not source.exists() and (tmp_path / "옮긴 곳" / ".git" / "obj").read_text(encoding="utf-8") == "읽기 전용"
        single = write(tmp_path / "하나.txt", "읽기 전용")
        os.chmod(single, stat.S_IREAD)
        organize.move(single, str(tmp_path / "다른 곳" / "하나.txt"))
        assert not os.path.exists(single) and (tmp_path / "다른 곳" / "하나.txt").exists()
    finally:
        for folder, _, names in os.walk(tmp_path):
            for name in names:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE)


def test_undo_does_not_enter_a_deleted_blocked_folder_and_recreates_parent(home, client, monkeypatch, tmp_path):
    write(home / "D" / "Old" / "a.txt")
    build(home)
    outside = tmp_path / "밖"
    outside.mkdir()
    client.post("/api/move", json={"paths": [str(home / "D" / "Old")], "folder": str(outside)})
    server.jobs.wait()
    (home / "D" / "Old").mkdir()
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    blacklist.add(str(home / "D" / "Old"), set())
    os.rmdir(home / "D" / "Old")  # 같은 자리를 차단한 뒤 탐색기에서 지웠다
    client.post("/api/undo", json={})
    server.jobs.wait()
    assert server.jobs.status["result"]["moved"] == 0 and (outside / "Old" / "a.txt").exists()
    write(home / "E" / "Sub" / "b.txt")
    (home / "F").mkdir()
    build(home)
    client.post("/api/move", json={"paths": [str(home / "E" / "Sub")], "folder": str(home / "F")})
    server.jobs.wait()
    os.rmdir(home / "E")  # 빈 위 폴더를 지웠다
    client.post("/api/undo", json={})
    server.jobs.wait()
    assert server.jobs.status["result"]["moved"] == 1 and (home / "E" / "Sub" / "b.txt").exists()


def test_bad_names_broken_exif_and_unplugged_drive_do_not_stop_or_wipe_the_index(home, tmp_path, monkeypatch):
    write(home / "a.txt", "보통 파일입니다 " * 5)
    write(home / "bad\udc80name.txt")  # 짝 없는 서로게이트 글자가 든 이름
    disk = tmp_path / "외장"
    write(disk / "사진.txt", "외장 디스크의 문서 내용입니다 " * 5)
    settings.save({"roots": [str(home), str(disk)]})
    build(home)
    db = index.connect()
    try:
        assert sorted(r[0] for r in db.execute("select name from files")) == ["a.txt", "사진.txt"]
    finally:
        db.close()
    os.rename(disk, tmp_path / "뽑음")  # 외장 디스크를 뽑았다
    build(home)
    db = index.connect()
    try:
        assert db.execute("select count(*) from files where name = '사진.txt'").fetchone()[0] == 1  # 지우지 않는다
    finally:
        db.close()
    picture = tmp_path / "그림.png"
    Image.new("RGB", (40, 30), (10, 200, 10)).save(picture)

    def broken(image):
        raise SyntaxError("not a TIFF file")

    monkeypatch.setattr(ImageOps, "exif_transpose", broken)
    assert media.image_hash(str(picture)) is None


def test_policy_and_subst_are_refused(tmp_path, monkeypatch):
    path = write(tmp_path / "a.txt")
    monkeypatch.setattr(trash, "policy_off", lambda: True)
    assert "정책" in trash.problem(path)
    monkeypatch.setattr(trash.drives, "dos_device", lambda drive: "\\??\\C:\\자료")
    assert trash.drives.is_subst("Q:\\a.txt")
    monkeypatch.setattr(trash.drives, "dos_device", lambda drive: "\\Device\\HarddiskVolume3")
    assert not trash.drives.is_subst("C:\\a.txt")


def test_finished_job_result_is_kept_when_the_next_starts():
    first = jobs.start("첫 작업", lambda progress: "첫 결과")
    jobs.wait()
    jobs.start("다음 작업", lambda progress: time.sleep(0.3) or "다음 결과")
    assert jobs.status["last"] == {"number": first["number"], "result": "첫 결과", "error": ""}
    jobs.wait()
