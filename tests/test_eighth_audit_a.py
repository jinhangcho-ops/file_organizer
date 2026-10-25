"""여덟 번째 감사(A)에서 막은 것: 작업 중에 꺼진 휴지통(영구 삭제), 보관 중에 바뀐 원본, 차단 폴더를 가리키는 정션,
아무것도 옮기지 못한 정리가 지우던 되돌리기 기록, 이름 없는 색인 단계 설정, 원본을 못 지웠을 때의 읽기 전용 표시, 보관 뒤 색인 실패.
파일은 모두 지어낸다. 실제 휴지통은 쓰지 않는다(fake_trash)."""
import os
import sqlite3
import stat
import subprocess
import time

import pytest

import actions
import archive
import browse
import drives
import exclude
import fence
import games
import index
import organize
import settings
import trash
from conftest import age, build, write


def turn_off_after_first(monkeypatch, fake_trash):
    """첫 파일을 보낸 뒤 휴지통이 꺼진 것처럼(NukeOnDelete). 그 뒤에 보내면 send2trash는 영구 삭제한다."""
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (bool(fake_trash), None))


@pytest.fixture
def old_files(home, tmp_path, monkeypatch):
    """보관할 오래된 파일 둘과 보관할 곳. 이 PC의 휴지통 설정 대신 '켜짐'으로 본다."""
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))
    paths = [write(home / "보고" / f"오래된{n}.txt", "가나다" * 100) for n in range(2)]
    time.sleep(1.5)  # 새 파일의 연 시각을 백신 검사가 바꾼 뒤에 날짜를 바꾼다
    for path in paths:
        age(path, 400)
    build(home)
    dest = tmp_path / "보관함"
    dest.mkdir()
    return paths, dest


def test_trash_many_rechecks_recycle_bin_during_job(tmp_path, monkeypatch, fake_trash):
    paths = [write(tmp_path / "캐시" / f"f{n}") for n in range(5)]
    turn_off_after_first(monkeypatch, fake_trash)
    monkeypatch.setattr(trash, "RECHECK", 0, raising=False)  # 시간이 흐른 것처럼: 파일마다 다시 검사한다
    result = actions.trash_many(paths)
    assert result["trashed"] == 1 and len(fake_trash) == 1
    assert all("휴지통을 쓰지 않도록" in f["error"] for f in result["failed"]) and len(result["failed"]) == 4
    assert all(os.path.exists(p) for p in paths[1:])


def test_archive_rechecks_recycle_bin_after_zip(old_files, monkeypatch, fake_trash):
    paths, dest = old_files
    real = archive.verify

    def verify_then_off(zip_path, written):  # zip을 만드는 동안(수십 분일 수 있다) 휴지통이 꺼졌다
        monkeypatch.setattr(trash, "recycle_settings", lambda path: (True, None))
        return real(zip_path, written)

    monkeypatch.setattr(archive, "verify", verify_then_off)
    result = archive.run(str(dest), 365)
    assert result["files"] == 2 and fake_trash == [] and result["freed"] == 0
    assert all(os.path.exists(p) for p in paths)


def test_archive_keeps_original_changed_after_zip(old_files, monkeypatch, fake_trash):
    paths, dest = old_files
    real = archive.verify

    def verify_then_edit(zip_path, written):  # zip을 확인하는 동안 다른 프로그램이 원본을 저장했다
        write(paths[0], "새 내용")
        return real(zip_path, written)

    monkeypatch.setattr(archive, "verify", verify_then_edit)
    result = archive.run(str(dest), 365)
    assert fake_trash == [paths[1]] and open(paths[0], encoding="utf-8").read() == "새 내용"
    assert [f["from"] for f in result["failed"]] == [paths[0]] and "바뀐" in result["failed"][0]["error"]


def test_archive_returns_result_when_index_update_fails(old_files, monkeypatch, fake_trash):
    _, dest = old_files

    def locked(paths):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(index, "removed", locked)
    result = archive.run(str(dest), 365)
    assert len(fake_trash) == 2 and result["failed"][-1]["from"] == "색인"


def test_junction_into_blocked_folder_is_refused(tmp_path, monkeypatch):
    target, link = tmp_path / "차단", tmp_path / "바로가기"
    (target / "안").mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    try:
        with monkeypatch.context() as m:
            m.setattr(fence, "keys", lambda: {exclude.key(str(target))})
            for call in (lambda: settings.folder_text(str(link), "정리할 곳"), lambda: browse.listing(str(link)),
                         lambda: browse.make_folder(str(link), "새 폴더")):
                with pytest.raises(ValueError, match="차단"):
                    call()
        with monkeypatch.context() as m:  # 게임 폴더를 가리키는 정션도
            m.setattr(fence, "keys", lambda: set())
            m.setattr(games, "keys", lambda: {exclude.key(str(target))})
            with pytest.raises(ValueError, match="게임"):
                settings.folder_text(str(link), "놓을 곳")
        assert not (target / "새 폴더").exists()
    finally:
        os.rmdir(link)  # 정션만 지운다(가리키는 폴더는 그대로)
    assert (target / "안").is_dir()


def test_organize_moving_nothing_keeps_undo_log(home, tmp_path, monkeypatch):
    settings.write(organize.LOG, [{"from": "C:\\예전\\a.txt", "to": "C:\\정리\\a.txt"}])
    source, dest = tmp_path / "받은 파일", tmp_path / "정리"
    write(source / "b.txt")
    dest.mkdir()

    def busy(before, after):
        raise OSError("다른 프로그램이 쓰는 중입니다")

    monkeypatch.setattr(organize, "move", busy)
    result = organize.run(str(source), str(dest))
    assert result["moved"] == 0 and result["failed"]
    assert organize.last() == [{"from": "C:\\예전\\a.txt", "to": "C:\\정리\\a.txt"}]  # [마지막 정리 되돌리기]가 남는다


@pytest.mark.parametrize("steps", [[{"name": 1, "on": True}] + [{"name": n, "on": True} for n in list(settings.STEPS)[1:]],
                                   [{"on": True}] + [{"name": n, "on": True} for n in list(settings.STEPS)[1:]]])
def test_index_steps_without_text_name_is_value_error(home, steps):
    with pytest.raises(ValueError, match="색인 단계"):
        settings.check({"index_steps": steps})


def test_original_stays_read_only_when_it_cannot_be_removed(tmp_path, monkeypatch):
    before, after = write(tmp_path / "가" / "읽기전용.txt"), str(tmp_path / "나" / "읽기전용.txt")
    os.chmod(before, stat.S_IREAD)
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)  # 다른 디스크처럼
    try:
        with open(before, "rb"):  # 다른 프로그램이 여는 중: 읽기는 되고 지우기는 안 된다
            with pytest.raises(OSError):
                organize.move(before, after)
        assert not os.stat(before).st_mode & stat.S_IWRITE and not os.path.exists(after)
    finally:
        os.chmod(before, stat.S_IWRITE)


def test_rename_route_says_when_index_was_not_updated(home, client, monkeypatch):
    """이름은 바뀌었는데 색인 반영이 실패하면(색인 중 DB 잠김) 화면에 알린다: 다음 색인 전까지 옮기기·휴지통이 거부된다."""
    write(home / "가" / "a.txt")
    build(home)
    answer = client.post("/api/rename", json={"path": str(home / "가" / "a.txt"), "name": "b.txt"}).json
    assert answer["path"] == str(home / "가" / "b.txt") and answer["note"] == ""
    def locked_db(pairs):
        raise sqlite3.OperationalError("database is locked")  # 색인기가 쓰기 잠금을 잡고 있을 때

    with monkeypatch.context() as m:
        m.setattr(index, "moved", locked_db)
        answer = client.post("/api/rename", json={"path": str(home / "가" / "b.txt"), "name": "c.txt"}).json
    assert (home / "가" / "c.txt").exists() and "색인" in answer["note"]
