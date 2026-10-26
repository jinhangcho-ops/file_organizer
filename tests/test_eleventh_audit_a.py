"""열한 번째 감사(A)에서 막은 것: 정션인 사진·동영상 폴더로 차단·잠금 폴더에 캡처를 옮기던 것, 시스템 폴더 검사가 부를 때마다 실제 경로를 찾던 것,
보내지 못한 것의 크기가 휴지통 합계에 남던 것, 휴지통에 이미 든 크기를 세지 않던 것, 옮긴 예외 폴더, 같은 곳으로 모이는 두 메모,
그리고 정션 경로·실제 경로를 둘 다 보는 줄이 빠지면 실패하는 검사들. 파일은 모두 지어낸다. 실제 휴지통에는 보내지 않는다."""
import json
import os
import subprocess
import tempfile

import pytest

import actions
import blacklist
import captures
import drives
import exclude
import fence
import games
import index
import lock
import notes
import organize
import settings
import trash
from conftest import build, write

REAL_FOLDERS = captures.folders  # home이 바꿔 끼우기 전의 것
REAL_BIN_SIZE = getattr(trash, "bin_size", None)  # 공통 준비가 0으로 바꿔 끼워도 읽기만 하는 진짜 것을 한 번 부른다


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def keys_of_index():
    db = index.connect()
    try:
        return {row[0] for row in db.execute("select key from files")}
    finally:
        db.close()


def lock_at(path):
    lock.save({"hash": None, "salt": None, "vault": "", "vault_open": False, "folders": [{"path": str(path), "open": True}]})


@pytest.mark.parametrize("how", ["blocked", "lock"])
def test_capture_through_junction_pictures_does_not_go_into_fenced_folder(home, tmp_path, monkeypatch, how):
    """사진 폴더(알려진 폴더)가 정션이고 그 실제 쪽의 스크린샷 폴더를 차단(실제 경로로 저장)하거나 잠갔다."""
    real_pics, user = tmp_path / "D_Pictures", tmp_path / "user"
    shots = real_pics / captures.SHOTS
    shots.mkdir(parents=True)
    user.mkdir()
    (tmp_path / "정리").mkdir()
    jpics, videos = user / "Pictures", user / "Videos"
    shot = write(videos / "Captures" / "Game 2026-10-24 14-33-00.png")
    download = write(tmp_path / "받은 파일" / "Game 2026-10-24 14-33-01.png")
    junction(jpics, real_pics)
    try:
        if how == "blocked":
            blacklist.folder().mkdir(parents=True, exist_ok=True)
            (blacklist.folder() / blacklist.FILE).write_text(json.dumps([str(shots)]), encoding="utf-8")
        else:
            lock_at(shots)
        monkeypatch.setattr(drives, "known_folder", lambda id_: str(jpics) if id_ == captures.PICTURES_ID else str(videos))
        monkeypatch.setattr(games, "value", lambda *args: None)  # 이 PC의 Steam은 읽지 않는다
        monkeypatch.setattr(captures, "folders", REAL_FOLDERS)
        assert organize.plan(str(tmp_path / "받은 파일"), str(tmp_path / "정리")) == []  # 다운로드 정리도 같은 폴더(captures.folders)를 쓴다
        assert captures.run()["moved"] == 0 and os.path.exists(shot) and os.path.exists(download)
        assert not any(names for _, _, names in os.walk(shots))
    finally:
        os.rmdir(jpics)


def test_system_folder_does_not_look_up_real_paths_every_call(home, monkeypatch):
    """환경 변수가 가리키는 폴더의 key는 값이 바뀔 때만 다시 만든다(부를 때마다 만들면 2,000번에 5초 넘게 걸렸다)."""
    calls = []
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda p, *a, **k: calls.append(p) or realpath(p, *a, **k))
    for _ in range(200):
        fence.system_folder(r"C:\Users\someone\Documents\a\b.txt")
    assert len(calls) < 20


def test_failed_send_does_not_stay_in_recycle_bin_total(home, tmp_path, monkeypatch):
    """휴지통 1,000바이트: 900바이트가 쓰는 중이라 못 갔으면 다음 200바이트는 간다."""
    big, small = write(tmp_path / "t" / "big.bin", "x" * 900), write(tmp_path / "t" / "small.txt", "x" * 200)
    monkeypatch.setattr(trash, "disk", lambda place: (None, 1000))
    monkeypatch.setattr(trash, "bin_size", lambda root: 0)
    sent = []

    def send(path):
        if path == big:
            raise OSError(32, "다른 프로세스가 사용 중")
        sent.append(path)
        os.rename(path, path + ".binned")

    monkeypatch.setattr(trash, "send", send)
    result = actions.trash_many([big, small])
    assert sent == [small] and result["trashed"] == 1


def test_total_starts_from_what_is_already_in_the_bin(home, tmp_path, monkeypatch, fake_trash):
    small = write(tmp_path / "t" / "small.txt", "x" * 200)
    monkeypatch.setattr(trash, "disk", lambda place: (None, 1000))
    monkeypatch.setattr(trash, "bin_size", lambda root: 900)  # 휴지통에 이미 900바이트
    assert trash.problem(small) == "휴지통에 자리가 모자랍니다(휴지통을 비우거나 최대 크기를 늘리세요)"
    monkeypatch.setattr(trash, "bin_size", lambda root: 0)
    assert trash.problem(small) is None


def test_bin_size_reads_the_system_drive_and_unknown_drive_is_zero():
    """읽기만 하는 SHQueryRecycleBinW: 시스템 드라이브는 0 이상, 없는 드라이브는 0."""
    assert REAL_BIN_SIZE(os.environ.get("SystemDrive", "C:") + "\\") >= 0
    missing = next(d + ":\\" for d in "QRSTUVWXYZ" if not os.path.exists(d + ":\\"))
    assert REAL_BIN_SIZE(missing) == 0


def test_total_is_kept_across_recheck(home, tmp_path, monkeypatch, fake_trash):
    """RECHECK=0(보낼 때마다 디스크를 다시 검사)이어도 이 작업에서 보낸 크기 합은 남는다."""
    a, b = write(tmp_path / "t" / "a.bin", "x" * 600), write(tmp_path / "t" / "b.bin", "x" * 600)
    monkeypatch.setattr(trash, "RECHECK", 0)
    monkeypatch.setattr(trash, "disk", lambda place: (None, 1000))
    monkeypatch.setattr(trash, "bin_size", lambda root: 0)
    sent, refused = trash.to_trash([a, b])
    assert sent == [a] and refused == [(b, "휴지통에 자리가 모자랍니다(휴지통을 비우거나 최대 크기를 늘리세요)")]


def test_moved_excluded_folder_keeps_its_exclusion(home, tmp_path):
    src, to = home / "ex" / "Keep", home / "ex" / "Dest"
    write(src / "old.txt")
    to.mkdir()
    exclude.add(str(src))
    assert actions.move_many([str(src)], str(to))["moved"] == 1
    assert [r["path"] for r in exclude.load()] == [os.path.realpath(to / "Keep")]


def test_junction_note_and_real_note_for_same_folder_are_joined(home, tmp_path):
    real, link = tmp_path / "D_Docs", tmp_path / "Docs"
    (real / "A").mkdir(parents=True)
    junction(link, real)
    try:
        settings.folder().mkdir(parents=True, exist_ok=True)
        settings.write(notes.FILE, {exclude.key(link / "A"): {"path": str(link / "A"), "text": "정션 쪽 메모"},
                                    exclude.key(real / "A"): {"path": str(real / "A"), "text": "실제 쪽 메모"}})
        assert notes.get(str(real / "A")) == "정션 쪽 메모\n실제 쪽 메모"
    finally:
        os.rmdir(link)


def test_moved_looks_up_each_folder_once(home, monkeypatch):
    """5,000개를 옮긴 뒤 파일마다 실제 경로를 찾으면 2.7초: 폴더마다 한 번만 찾는다."""
    calls = []
    real = index.real
    monkeypatch.setattr(index, "real", lambda p: calls.append(p) or real(p))
    actions.moved([(str(home / "a" / f"{n}.txt"), str(home / "b" / f"{n}.txt")) for n in range(100)])
    assert len(calls) < 20


def test_lock_check_new_records_real_path_and_sees_old_junction_record(home, tmp_path):
    real, link = tmp_path / "real", tmp_path / "link"
    (real / "L").mkdir(parents=True)
    (real / "M").mkdir()
    junction(link, real)
    try:
        assert lock.check_new(str(link / "M"), set()) == os.path.realpath(real / "M")
        lock_at(link / "L")  # 예전 판이 정션 경로로 적어 둔 잠금 폴더
        with pytest.raises(ValueError, match="겹칩니다"):
            lock.check_new(str(real / "L"), set())
    finally:
        os.rmdir(link)


def test_blacklist_route_removes_index_rows_under_real_path(home, tmp_path, monkeypatch, client):
    real, link = tmp_path / "real", tmp_path / "link"
    write(real / "secret" / "a.txt")
    junction(link, real)
    try:
        settings.save({"roots": [str(real)]})
        build(home)
        assert any("secret" in k for k in keys_of_index())
        monkeypatch.setattr(blacklist, "is_admin", lambda: True)
        assert client.post("/api/blacklist", json={"path": str(link / "secret")}).status_code == 200
        assert not any("secret" in k for k in keys_of_index())
    finally:
        os.rmdir(link)


def test_launcher_game_by_junction_path_guards_real_path(home, tmp_path, monkeypatch):
    real, link = tmp_path / "real", tmp_path / "link"
    (real / "Game").mkdir(parents=True)
    junction(link, real)
    try:
        monkeypatch.setattr(games, "launcher_games", lambda: [{"path": str(link / "Game")}])
        games.forget()
        with pytest.raises(ValueError, match="게임 폴더"):
            fence.refuse_system(str(real / "Game" / "save.dat"))
    finally:
        os.rmdir(link)
        games.forget()


def test_system_place_by_junction_guards_real_path(home, tmp_path, monkeypatch):
    real, link = tmp_path / "real", tmp_path / "link"
    (real / "Local").mkdir(parents=True)
    junction(link, real)
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(link / "Local"))
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))  # 검사 파일은 실제 %TEMP% 안에 있다: 그 밖인 것처럼
        assert fence.system_folder(str(real / "Local" / "app" / "a.dll"))
    finally:
        os.rmdir(link)


def test_temp_by_junction_inside_system_place_is_not_system(home, tmp_path, monkeypatch):
    real, link = tmp_path / "real", tmp_path / "link"
    (real / "Local" / "Temp").mkdir(parents=True)
    junction(link, real)
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(real / "Local"))
        monkeypatch.setattr(tempfile, "tempdir", str(link / "Local" / "Temp"))
        assert fence.system_folder(str(real / "Local" / "app.dll"))
        assert not fence.system_folder(str(real / "Local" / "Temp" / "a.txt"))
    finally:
        os.rmdir(link)


def test_other_profile_by_junction_guards_real_profile(home, tmp_path, monkeypatch):
    """다른 계정의 사용자 폴더(drives.profile_folders)만 정션 경로다. 환경 변수는 그쪽을 가리키지 않는다."""
    real, link = tmp_path / "real_users", tmp_path / "users"
    (real / "other" / "AppData").mkdir(parents=True)
    junction(link, real)
    try:
        monkeypatch.setattr(drives, "profile_folders", lambda: [str(link / "other")])
        with pytest.raises(ValueError, match="Windows·프로그램 폴더가 들어 있어"):
            fence.refuse_holding(str(real / "other"), True, moving=True)
    finally:
        os.rmdir(link)


def test_lock_by_junction_path_guards_real_parent(home, tmp_path):
    real, link = tmp_path / "real", tmp_path / "link"
    (real / "X" / "L").mkdir(parents=True)
    junction(link, real)
    try:
        lock_at(link / "X" / "L")
        with pytest.raises(ValueError, match="잠금 폴더"):
            fence.refuse_holding(str(real / "X"), False)
    finally:
        os.rmdir(link)
