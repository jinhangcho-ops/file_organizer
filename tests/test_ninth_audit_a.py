"""아홉 번째 감사(A)에서 막은 것: 다른 디스크로 폴더를 옮기는 중 사라진 원본, 오래 걸린 크기 세기 동안 꺼진 휴지통,
차단·게임 폴더를 가리키는 정션(꺼낼 곳·차단 목록·구조 보호 목록), 보관이 받아 오던 클라우드 전용 파일.
파일은 모두 지어낸다. 실제 휴지통은 쓰지 않는다(fake_trash, trash.problem만 부른다)."""
import os
import subprocess
import time

import pytest

import archive
import blacklist
import drives
import exclude
import filetime
import games
import index
import lock
import organize
import protect
import trash
from conftest import age, build, write
from test_blocked_and_locked_folders import PASSWORD


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_move_folder_across_drives_survives_original_gone_after_copy(tmp_path, monkeypatch):
    before, after = tmp_path / "가", tmp_path / "나" / "가"
    for name in "abc":
        write(before / f"{name}.txt", name)
    real = filetime.copy

    def copy_then_delete_b(source, target):  # c를 복사하는 동안 다른 프로그램이 b를 지웠다
        if source.endswith("c.txt"):
            os.remove(before / "b.txt")
        return real(source, target)

    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)
    monkeypatch.setattr(filetime, "copy", copy_then_delete_b)
    with pytest.raises(organize.Partial):  # Partial이어야 옮긴 곳이 색인·잠금·메모·되돌리기에 들어간다(actions.move_each)
        organize.move_folder(str(before), str(after))
    assert sorted(os.listdir(after)) == ["a.txt", "b.txt", "c.txt"]
    assert not (before / "a.txt").exists() and not (before / "c.txt").exists()  # 멈추지 않고 나머지 원본도 지웠다


def test_problem_rechecks_recycle_bin_after_long_size_walk(tmp_path, monkeypatch):
    folder = tmp_path / "큰 폴더"
    write(folder / "a.txt")
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))
    real = trash.size_of

    def slow_walk(path):  # 수십만 개를 세는 동안 휴지통이 꺼졌다
        monkeypatch.setattr(trash, "recycle_settings", lambda path: (True, None))
        return real(path)

    monkeypatch.setattr(trash, "size_of", slow_walk)
    monkeypatch.setattr(trash, "RECHECK", 0)  # 세기가 RECHECK초보다 오래 걸린 것처럼
    reason = trash.problem(str(folder))
    assert reason and "휴지통을 쓰지 않도록" in reason


def test_problem_refuses_disk_without_bin_before_walking(tmp_path, monkeypatch):
    write(tmp_path / "폴더" / "a.txt")
    monkeypatch.setattr(trash, "disk", lambda path: ("휴지통이 없는 디스크입니다", 0))
    monkeypatch.setattr(trash, "size_of", lambda path: pytest.fail("휴지통이 없는데 폴더를 끝까지 셌다"))
    assert trash.problem(str(tmp_path / "폴더")) == "휴지통이 없는 디스크입니다"


@pytest.fixture
def locked(home, client):
    client.post("/api/lock/password", json={"new": PASSWORD})
    return home


def test_take_out_refuses_junction_into_game_folder(locked, client, monkeypatch, unlock_after):
    write(locked / "모음" / "a.txt")
    build(locked)
    place = locked.parent / "금고"
    place.mkdir()
    client.post("/api/lock/vault", json={"password": PASSWORD, "place": str(place)})
    item = str(locked / "모음")
    client.post("/api/lock", json={"path": item, "password": PASSWORD, "vault": True})
    target, link = locked / "게임", locked / "바로가기"
    target.mkdir()
    junction(link, target)
    try:
        with monkeypatch.context() as m:
            m.setattr(games, "keys", lambda: {exclude.key(str(target))})
            with pytest.raises(ValueError, match="게임"):
                lock.take_out(item, PASSWORD, str(link))
        assert os.listdir(target) == [] and [f["path"] for f in lock.listing()] == [item]  # 금고에 그대로
    finally:
        os.rmdir(link)  # 정션만 지운다(가리키는 폴더는 그대로)


@pytest.mark.parametrize("module", [blacklist, protect])
def test_blocked_and_protected_lists_store_the_real_folder(home, monkeypatch, module):
    monkeypatch.setattr(blacklist, "is_admin", lambda: True)
    target, link = home / "진짜", home / "바로가기"
    target.mkdir()
    junction(link, target)
    try:
        module.add(str(link), set()) if module is blacklist else module.add(str(link))
        assert module.load() == [str(target)]  # 정션 경로를 넣으면 진짜 폴더가 막히지 않는다
    finally:
        os.rmdir(link)


def test_archive_skips_cloud_only_files(home, tmp_path, monkeypatch, fake_trash):
    monkeypatch.setattr(trash, "recycle_settings", lambda path: (False, None))
    paths = [write(home / "보고" / f"오래된{n}.txt", "가나다" * 100) for n in range(2)]
    time.sleep(1.5)  # 새 파일의 연 시각을 백신 검사가 바꾼 뒤에 날짜를 바꾼다
    for path in paths:
        age(path, 400)
    build(home)
    dest = tmp_path / "보관함"
    dest.mkdir()
    online = os.stat(paths[0]).st_ino  # paths[0]만 온라인 전용인 것처럼
    with monkeypatch.context() as m:
        m.setattr(index, "cloud_only", lambda st: st.st_ino == online)
        assert archive.preview(365, str(dest))["files"] == 1
        result = archive.run(str(dest), 365)
    assert result["files"] == 1 and fake_trash == [paths[1]] and os.path.exists(paths[0])
