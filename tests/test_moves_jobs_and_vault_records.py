"""3차 감사 뒤 고친 것들: 다른 디스크로 폴더 옮기기에서 복사하는 동안 생긴 파일은 지우지 않기, 작업 중 차단·보호·잠금 바꾸기 거부,
금고에 든 폴더의 원래 위치도 위 폴더를 따라 고치기, 금고를 다시 잠그지 못해도 기록 남기기, 되돌리기는 차단·잠금 폴더를 건드리지 않기."""
import os

import pytest

import blacklist
import filetime
import jobs
import lock
import organize
import server
from conftest import build, write
from test_blocked_and_locked_folders import PASSWORD


def test_folder_move_to_another_drive_keeps_files_that_appear_during_copy(tmp_path, monkeypatch):
    source = tmp_path / "보낼 폴더"
    write(source / "a.txt", "가")
    write(source / "안" / "b.txt", "나")
    late = source / "받는 중.txt"
    real_copy = filetime.copy

    def copy_then_new_file(src, dst):
        real_copy(src, dst)
        if not late.exists():
            late.write_text("복사하는 동안 생겼다", encoding="utf-8")

    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)
    monkeypatch.setattr(organize.filetime, "copy", copy_then_new_file)
    target = tmp_path / "다른 디스크" / "보낼 폴더"
    with pytest.raises(OSError, match="원래 폴더에 남겼습니다"):
        organize.move_folder(str(source), str(target))
    assert late.read_text(encoding="utf-8") == "복사하는 동안 생겼다"  # 영구 삭제되지 않았다
    assert sorted(os.listdir(source)) == ["받는 중.txt"]  # 복사한 것과 빈 폴더만 지웠다
    assert (target / "a.txt").read_text(encoding="utf-8") == "가" and (target / "안" / "b.txt").read_text(encoding="utf-8") == "나"


def test_folder_move_to_another_drive_moves_everything(tmp_path, monkeypatch):
    source = tmp_path / "보낼 폴더"
    write(source / "안" / "b.txt", "나")
    (source / "빈 폴더").mkdir()
    monkeypatch.setattr(organize.drives, "same_drive", lambda a, b: False)
    target = tmp_path / "다른 디스크" / "보낼 폴더"
    organize.move_folder(str(source), str(target))
    assert not source.exists() and (target / "안" / "b.txt").exists() and (target / "빈 폴더").is_dir()


def test_no_fence_changes_while_a_job_runs(home, client, monkeypatch):
    write(home / "가" / "a.txt")
    build(home)
    client.post("/api/lock/password", json={"new": PASSWORD})
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    monkeypatch.setitem(jobs.status, "running", True)
    monkeypatch.setitem(jobs.status, "name", "휴지통으로 보내기")
    for url, body in (("/api/lock", {"path": str(home / "가"), "password": PASSWORD}), ("/api/blacklist", {"path": str(home / "가")}),
                      ("/api/protect", {"path": str(home / "가")})):
        answer = client.post(url, json=body)
        assert answer.status_code == 400 and "작업이 끝난 뒤" in answer.json["error"], url
    assert lock.listing() == [] and blacklist.load() == []


@pytest.fixture
def vault(home, client, monkeypatch):
    client.post("/api/lock/password", json={"new": PASSWORD})
    place = home.parent / "금고 둘 곳"
    place.mkdir()
    monkeypatch.setattr(server, "ask_folder", lambda: str(place))
    return client.post("/api/lock/vault", json={"password": PASSWORD}).json["vault"]


def test_vault_item_follows_its_parent_folder(home, client, vault, unlock_after):
    write(home / "위" / "모음" / "a.txt")
    write(home / "위" / "b.txt")
    build(home)
    client.post("/api/lock", json={"path": str(home / "위" / "모음"), "password": PASSWORD, "vault": True})
    renamed = client.post("/api/rename", json={"path": str(home / "위"), "name": "아래"}).json["path"]
    assert [f["path"] for f in lock.listing()] == [os.path.join(renamed, "모음")]
    back = client.post("/api/lock/take_out", json={"path": os.path.join(renamed, "모음"), "password": PASSWORD}).json["path"]
    assert back == os.path.join(renamed, "모음") and os.listdir(back) == ["a.txt"]


def test_record_stays_when_the_vault_cannot_be_sealed_again(home, client, vault, monkeypatch, unlock_after):
    write(home / "모음" / "a.txt")
    build(home)

    def fail(path):
        raise OSError("다시 잠그지 못함")

    with monkeypatch.context() as m:
        m.setattr(lock, "seal", fail)
        answer = client.post("/api/lock", json={"path": str(home / "모음"), "password": PASSWORD, "vault": True})
    assert answer.status_code == 400
    assert lock.listing() == [{"path": str(home / "모음"), "open": True, "vault": True, "missing": False}]  # 기록과 열림이 남았다
    lock.close_all()  # 다음에 켤 때처럼 다시 잠근다
    assert lock.vault_info()["open"] is False
    with pytest.raises(PermissionError):
        os.listdir(vault)


def test_undo_leaves_files_in_a_folder_blocked_since(home, client, monkeypatch):
    source, dest = home / "받은 곳", home / "정리된 곳"
    write(source / "메모.txt")
    dest.mkdir()
    build(home)
    assert organize.run(str(source), str(dest))["moved"] == 1
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    client.post("/api/blacklist", json={"path": str(dest / "문서")})
    result = organize.undo()
    assert result["moved"] == 0 and "차단하거나 잠근" in result["failed"][0]["error"]
    assert (dest / "문서" / "메모.txt").exists() and len(organize.last()) == 1  # 기록은 남아 차단을 푼 뒤 되돌릴 수 있다


def test_new_folder_at_a_vault_items_place_cannot_take_its_record(home, client, vault, unlock_after):
    write(home / "사진" / "a.jpg")
    build(home)
    photos = str(home / "사진")
    client.post("/api/lock", json={"path": photos, "password": PASSWORD, "vault": True})
    write(home / "사진" / "새것.jpg")  # 원래 자리에 같은 이름의 새 폴더
    build(home)
    for vault_flag in (False, True):
        answer = client.post("/api/lock", json={"path": photos, "password": PASSWORD, "vault": vault_flag})
        assert answer.status_code == 400 and "같은 원래 위치" in answer.json["error"]
    assert lock.listing() == [{"path": photos, "open": False, "vault": True, "missing": False}]  # 금고 기록이 그대로


def test_partly_moved_folder_is_indexed_where_it_went(home, client, monkeypatch):
    write(home / "보낼" / "a.txt", "가")
    (home / "받을").mkdir()
    build(home)

    def partial(before, after):
        os.makedirs(after)
        os.rename(os.path.join(before, "a.txt"), os.path.join(after, "a.txt"))
        raise organize.Partial("일부 남김")

    monkeypatch.setattr(organize, "move_folder", partial)
    job = client.post("/api/move", json={"paths": [str(home / "보낼")], "folder": str(home / "받을")}).json["job"]
    result = server.jobs.wait()["result"]
    assert job and result["moved"] == 1 and result["failed"][0]["error"] == "일부 남김"
    listed = [row["path"] for row in client.post("/api/files", json={}).json["rows"]]
    assert listed == [str(home / "받을" / "보낼" / "a.txt")]


def test_move_that_would_put_a_lock_on_a_vault_items_place_is_refused(home, client, vault, unlock_after):
    write(home / "가" / "사진" / "a.jpg")
    write(home / "나" / "사진" / "b.jpg")
    write(home / "나" / "c.txt")
    build(home)
    client.post("/api/lock", json={"path": str(home / "가" / "사진"), "password": PASSWORD, "vault": True})
    client.post("/api/lock", json={"path": str(home / "나" / "사진"), "password": PASSWORD})
    os.rmdir(home / "가")  # 금고에 든 폴더의 원래 위 폴더가 사라졌다
    answer = client.post("/api/rename", json={"path": str(home / "나"), "name": "가"})
    assert answer.status_code == 400 and "원래 위치와 같아집니다" in answer.json["error"]
    assert os.path.isdir(home / "나") and len(lock.listing()) == 2
