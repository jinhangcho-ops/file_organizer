"""일곱 번째 감사(A)에서 막은 것: 다른 디스크로 옮길 때 읽기 전용 원본·읽을 수 없는 하위 폴더, 색인 반영이 실패해도 남는 기록,
캐시 정리의 디스크 검사 횟수, 휴지통 크기 정책, 확장자가 긴 파일의 휴지통 경로, 끝에 \\가 붙은 경로.
파일은 모두 지어낸다. 실제 휴지통은 쓰지 않는다(fake_trash, 또는 trash.problem만 부른다)."""
import collections
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

import actions
import drives
import index
import lock
import notes
import organize
import server
import trash
import undo
from conftest import build, finish, write


def locked_db(*args):
    raise sqlite3.OperationalError("database is locked")  # 색인기가 쓰기 잠금을 잡고 있을 때


def test_read_only_file_moves_to_other_drive(tmp_path, monkeypatch):
    before, after = Path(write(tmp_path / "가" / "읽기전용.txt")), tmp_path / "나" / "읽기전용.txt"
    os.chmod(before, stat.S_IREAD)
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)  # 다른 디스크처럼
    organize.move(str(before), str(after))
    assert not before.exists() and after.read_text(encoding="utf-8") == "내용"
    assert not os.stat(after).st_mode & stat.S_IWRITE  # 사본에는 읽기 전용 표시가 그대로
    os.chmod(after, stat.S_IWRITE)


def test_read_only_file_in_folder_moves_to_other_drive(tmp_path, monkeypatch):
    inside = Path(write(tmp_path / "가" / "안" / "a.txt"))
    os.chmod(inside, stat.S_IREAD)
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)
    organize.move_folder(str(tmp_path / "가"), str(tmp_path / "나"))  # Partial이 나지 않는다
    assert not (tmp_path / "가").exists() and (tmp_path / "나" / "안" / "a.txt").exists()
    os.chmod(tmp_path / "나" / "안" / "a.txt", stat.S_IWRITE)


def test_unreadable_subfolder_stops_move_to_other_drive(tmp_path, monkeypatch, unlock_after):
    write(tmp_path / "겉" / "a.txt")
    write(tmp_path / "겉" / "안" / "b.txt")
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)
    sid = lock.user_sid()
    subprocess.run(["icacls", str(tmp_path / "겉" / "안"), "/deny", f"*{sid}:(RD)"], check=True, capture_output=True)
    try:
        with pytest.raises(OSError, match="읽을 수 없는") as caught:
            organize.move_folder(str(tmp_path / "겉"), str(tmp_path / "나"))
        assert not isinstance(caught.value, organize.Partial)  # 아무것도 옮기지 않았다
        assert (tmp_path / "겉" / "a.txt").exists() and not (tmp_path / "나").exists()
    finally:
        subprocess.run(["icacls", str(tmp_path / "겉" / "안"), "/remove:d", f"*{sid}"], capture_output=True)
    assert (tmp_path / "겉" / "안" / "b.txt").exists()


def test_move_keeps_records_when_index_update_fails(home, monkeypatch):
    folder, dest = home / "가", home / "나"
    write(folder / "a.txt")
    dest.mkdir()
    notes.put(str(folder), "메모")
    monkeypatch.setattr(index, "moved", locked_db)
    result = actions.move_many([str(folder)], str(dest))
    assert result["moved"] == 1 and "색인" in result["failed"][0]["error"]
    assert notes.get(str(dest / "가")) == "메모"  # 메모(잠금 기록과 같은 자리)는 따라갔다
    assert undo.history[-1]["kind"] == "move"  # Ctrl+Z로 되돌릴 수 있다


def test_rename_keeps_records_when_index_update_fails(home, monkeypatch):
    folder = home / "가"
    write(folder / "a.txt")
    notes.put(str(folder), "메모")
    monkeypatch.setattr(index, "moved", locked_db)
    assert actions.rename(str(folder), "다") == str(home / "다")
    assert notes.get(str(home / "다")) == "메모" and undo.history[-1]["kind"] == "rename"


def test_organize_keeps_log_when_index_update_fails(tmp_path, monkeypatch):
    before = write(tmp_path / "가" / "a.txt")
    monkeypatch.setattr(index, "moved", locked_db)
    done, failed = organize.apply([(before, str(tmp_path / "나" / "a.txt"))])
    assert len(done) == 1 and "색인" in failed[0]["error"]


def test_trash_keeps_records_when_index_update_fails(home, monkeypatch, fake_trash):
    folder = home / "가"
    write(folder / "a.txt")
    notes.put(str(folder), "메모")
    monkeypatch.setattr(index, "removed", locked_db)
    result = actions.trash_many([str(folder)])
    assert result["trashed"] == 1 and "색인" in result["failed"][0]["error"]
    assert notes.get(str(folder)) == ""


def test_trash_checks_each_disk_once(tmp_path, monkeypatch, fake_trash):
    paths = [write(tmp_path / "캐시" / f"f_{n:06}") for n in range(30)]
    calls = collections.Counter()
    real = drives.drive_type
    monkeypatch.setattr(drives, "drive_type", lambda path: calls.update(["drive_type"]) or real(path))
    result = actions.trash_many(paths)
    assert result["trashed"] == 30 and calls["drive_type"] == 1  # 정책·레지스트리·볼륨은 디스크마다 한 번


def test_trash_obeys_recycle_bin_size_policy(tmp_path, monkeypatch):
    path = write(tmp_path / "a.txt", "x" * 2000)
    disk = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(trash.shutil, "disk_usage", lambda root: disk(100_000, 0, 100_000))
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))
    assert trash.problem(path) is None  # 정책이 없으면 디스크의 5%(5,000바이트)
    monkeypatch.setattr(trash, "policy_values", lambda name: [1] if name == "RecycleBinSize" else [])
    assert "최대 크기" in trash.problem(path)  # 정책 1% = 1,000바이트


def test_trash_counts_file_extension_in_bin_name(tmp_path, monkeypatch):
    long_ext = write(tmp_path / ("보고서." + "x" * 20))  # 휴지통 안 이름은 $R……에 원래 확장자가 붙는다
    plain = write(tmp_path / "보고서")
    monkeypatch.setattr(trash, "BIN_PREFIX", trash.MAX_PATH - (trash.width(drives.volume_root(plain)) - 3) - 5)  # 확장자 없이는 255
    assert "너무 길어" in trash.problem(long_ext)
    assert trash.problem(plain) is None


def test_checked_drops_trailing_backslash(tmp_path):
    folder = tmp_path / "A"
    folder.mkdir()
    assert server.checked(str(folder) + "\\", found={str(folder) + "\\": [1, 0]}) == str(folder)


def test_move_with_trailing_backslash_keeps_name(home, client):
    write(home / "A" / "a.txt")
    (home / "B").mkdir()
    build(home)
    response = client.post("/api/move", json={"paths": [str(home / "A") + "\\"], "folder": str(home / "B")})
    assert response.status_code == 200, response.get_json()
    finish()
    assert (home / "B" / "A" / "a.txt").exists() and not (home / "B" / " (2)").exists()
