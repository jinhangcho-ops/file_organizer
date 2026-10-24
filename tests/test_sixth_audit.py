"""여섯 번째 감사에서 막은 것: 옮기는 동안 바뀐 원본, 안을 읽을 수 없는 폴더·확장자가 붙는 폴더의 휴지통 검사, 정션·이상한 이름의 게임 캡처,
장치 이름·끝의 점, 시스템 폴더 안의 새 폴더, 날짜가 깨진 사진, 늘 열 수 없는 사진, 잠긴 사진의 인식, 다 치우지 못한 사본 알림.
파일은 모두 지어낸다. 실제 휴지통은 쓰지 않는다(trash.problem만 부른다)."""
import ctypes
import os
import shutil
import subprocess
import types
from pathlib import Path

import pytest

import actions
import browse
import captures
import drives
import exclude
import filetime
import games
import index
import lock
import media
import organize
import provenance
import recognize
import server
import trash
from conftest import build, write


def test_source_changed_while_copying_is_kept(tmp_path, monkeypatch):
    before, after = Path(write(tmp_path / "가" / "작업.txt", "old version!")), tmp_path / "나" / "작업.txt"
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)  # 다른 디스크처럼
    real_copy = filetime.copy

    def copy_then_save(source, target):
        real_copy(source, target)
        with open(source, "w", encoding="utf-8") as f:  # 복사 직후 다른 프로그램이 같은 길이로 저장한다
            f.write("new version!")
        os.utime(source, ns=(os.stat(source).st_atime_ns, os.stat(source).st_mtime_ns + 10 ** 9))

    monkeypatch.setattr(filetime, "copy", copy_then_save)
    with pytest.raises(OSError, match="원본이 바뀌어"):
        organize.move(str(before), str(after))
    assert before.read_text(encoding="utf-8") == "new version!" and not after.exists()


def test_trash_refuses_folder_with_unreadable_inside(tmp_path, unlock_after):
    write(tmp_path / "겉" / "안" / "a.txt", "x" * 1000)
    sid = lock.user_sid()
    subprocess.run(["icacls", str(tmp_path / "겉" / "안"), "/deny", f"*{sid}:(RD)"], check=True, capture_output=True)
    try:
        assert "읽을 수 없는" in trash.problem(str(tmp_path / "겉"))
    finally:
        subprocess.run(["icacls", str(tmp_path / "겉" / "안"), "/remove:d", f"*{sid}"], capture_output=True)


def test_trash_counts_folder_extension_in_bin_name(tmp_path, monkeypatch):
    folder = tmp_path / "보고서.backup_final"  # 휴지통 안 이름은 $R…에 원래 확장자(.backup_final)가 붙는다
    write(folder / "a.txt")
    rel = trash.size_of(str(folder))[1] - trash.width(str(folder))
    monkeypatch.setattr(trash, "BIN_PREFIX", trash.MAX_PATH - rel - (trash.width(drives.volume_root(str(folder))) - 3) - 5)  # 확장자 없이는 255
    assert "너무 길어" in trash.problem(str(folder))
    plain = tmp_path / "보고서"
    write(plain / "a.txt")
    assert trash.problem(str(plain)) is None  # 확장자가 없으면 같은 길이로 들어간다


def test_amd_captures_skip_junctions_and_odd_names(tmp_path):
    secret = Path(write(tmp_path / "차단" / "private.png"))
    game = tmp_path / "Radeon ReLive" / "MyGame"
    write(game / "clip.mp4")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(game / "link"), str(secret.parent)], check=True, capture_output=True)
    open(str(game / "bad\udc80.mp4"), "w").close()
    try:
        assert [os.path.basename(p) for p in captures.tree_files(str(game))] == ["clip.mp4"]
    finally:
        os.rmdir(game / "link")  # 정션만 뗀다(가리키는 폴더는 그대로)


@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1", "AUX.docx", "끝에 점."])
def test_device_names_are_not_made(name):
    with pytest.raises(ValueError, match="쓸 수 없는 이름"):
        actions.check_name(name)


def test_trailing_dot_path_is_not_handled(tmp_path):
    write(tmp_path / "abc", "옆의 다른 파일")
    odd = str(tmp_path / "abc.")
    open("\\\\?\\" + odd, "w").close()  # 끝의 점은 \\?\로만 만들 수 있다
    try:
        with pytest.raises(ValueError, match="다룰 수 없는 이름"):
            server.checked(odd, found={odd: [1]})  # 색인에 있다고 친다
    finally:
        os.remove("\\\\?\\" + odd)


def test_no_new_folder_inside_game_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "keys", lambda: {exclude.key(str(tmp_path))})
    with pytest.raises(ValueError, match="게임 폴더"):
        browse.make_folder(str(tmp_path), "새 폴더")
    assert not (tmp_path / "새 폴더").exists()


def picture(path):
    from PIL import Image
    img = Image.new("RGB", (40, 30), (10, 200, 10))
    img.paste((200, 10, 10), (0, 0, 20, 30))
    img.save(path)
    return path


def test_broken_date_picture_is_done_once(home):
    old = picture(home / "1601년.png")
    os.utime(old, ns=(0, -11644473600 * 10 ** 9 + 100))  # 나노초가 64비트 정수 밖: 저장할 때 자른다
    build(home)
    build(home)  # 두 번째 색인에서 바뀐 파일로 보면 지문을 지우고 다시 한다
    db = index.connect()
    try:
        assert db.execute("select count(*) from media m join files f on f.id = m.id where f.name = '1601년.png'").fetchone()[0] == 1
        assert len(media.todo(db, 30)) == 0
    finally:
        db.close()


def test_unreadable_picture_is_recorded_not_retried(home, unlock_after):
    path = picture(home / "못읽음.png")
    build(home)
    db = index.connect()
    sid = lock.user_sid()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = '못읽음.png'").fetchone())
        db.execute("delete from media where id = ?", (row["id"],))
        subprocess.run(["icacls", str(path), "/deny", f"*{sid}:(R)"], check=True, capture_output=True)  # 권한이 아예 없다
        media.fingerprint(db, row, 30)  # 다시 해 볼 오류가 아니다: 올리지 않고 빈 결과로
        assert db.execute("select frames from media where id = ?", (row["id"],)).fetchone()[0] == ""
    finally:
        subprocess.run(["icacls", str(path), "/remove:d", f"*{sid}"], capture_output=True)
        db.close()


def test_locked_picture_is_recognized_later(home):
    path = picture(home / "잠긴.png")
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = '잠긴.png'").fetchone())
        handle = ctypes.windll.kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)  # 공유 없이 연다
        try:
            with pytest.raises(OSError):
                recognize.recognize_one(db, types.SimpleNamespace(np=None), row, {}, None)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        assert db.execute("select count(*) from recognized where id = ?", (row["id"],)).fetchone()[0] == 0
    finally:
        db.close()


def test_move_folder_says_copies_were_left(tmp_path, monkeypatch):
    write(tmp_path / "가" / "a.txt")
    write(tmp_path / "가" / "b.txt")
    monkeypatch.setattr(drives, "same_drive", lambda a, b: False)
    real_copy, calls = filetime.copy, []

    def fail_second(source, target):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("시험: 복사 실패")
        real_copy(source, target)

    monkeypatch.setattr(filetime, "copy", fail_second)
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: None)  # 다른 프로그램이 열고 있어 하나도 치우지 못한 것처럼
    with pytest.raises(OSError, match="일부 사본이 남았습니다"):
        organize.move_folder(str(tmp_path / "가"), str(tmp_path / "나"))
    assert (tmp_path / "가" / "a.txt").exists() and (tmp_path / "가" / "b.txt").exists()


def test_under_matches_fence_inside():
    keys = {exclude.key(r"C:\가\나"), exclude.key("D:\\")}
    for path in (r"C:\가\나", r"C:\가\나\다\e.txt", r"C:\가\나다", r"C:\가", r"D:\x\y", r"E:\x"):
        assert index.under(exclude.key(path), keys) == trash_free_inside(path, keys), path


def trash_free_inside(path, keys):
    from fence import inside
    return inside(path, keys)


def test_broken_heic_does_not_stop_the_steps(monkeypatch, tmp_path):
    from PIL import Image
    path = str(picture(tmp_path / "깨진.png"))

    def broken(*args, **kwargs):
        raise RuntimeError("Security limit exceeded")  # pillow_heif가 깨진 HEIC에서 내는 오류

    monkeypatch.setattr(Image, "open", broken)
    assert media.image_hash(path) is None
    with pytest.raises(ValueError):  # 모델 오류(RuntimeError)와 나눠 파일 문제로
        recognize.open_image(path)


def test_broken_zstd_document_is_skipped(tmp_path):
    import zipfile
    doc = tmp_path / "깨진.docx"
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_ZSTANDARD) as z:
        z.writestr("word/document.xml", "본문 " * 2000)
        z.writestr("docProps/core.xml", "<x>" + "가" * 2000 + "</x>")
    data = bytearray(doc.read_bytes())
    for name in ("word/document.xml", "docProps/core.xml"):
        start = data.find(name.encode()) + len(name)  # 지역 머리의 이름 바로 뒤가 압축 조각(덧붙임 칸 없음)
        data[start:start + 4] = b"\0\0\0\0"  # 앞 4바이트(zstd 머리)를 깨뜨린다
    doc.write_bytes(bytes(data))
    assert index.doc_fingerprint(str(doc)) == "" and provenance.office_people(str(doc)) == ("", "")


def test_picture_blocked_while_reading_is_tried_again(home):
    import msvcrt
    path = picture(home / "일부잠금.png")
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = '일부잠금.png'").fetchone())
        db.execute("delete from media where id = ?", (row["id"],))
        with open(path, "rb+") as held:  # 다른 프로그램이 파일 일부를 잠근 것처럼(열기는 되고 읽기가 막힌다)
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, os.path.getsize(path))
            try:
                with pytest.raises(OSError):
                    media.fingerprint(db, row, 30)
                with pytest.raises(OSError):
                    recognize.recognize_one(db, types.SimpleNamespace(np=None, faces=None, custom=None), row, {}, None)
            finally:
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, os.path.getsize(path))
        assert db.execute("select count(*) from media where id = ?", (row["id"],)).fetchone()[0] == 0  # 빈 지문으로 굳지 않는다
        media.fingerprint(db, row, 30)
        assert db.execute("select frames from media where id = ?", (row["id"],)).fetchone()[0] != ""
    finally:
        db.close()


def test_size_condition_does_not_use_size_index(home):
    import search
    import settings
    write(home / "a.txt")
    build(home)
    parts, values, _ = search.conditions({"size_min": "0.001", "size_max": "100"}, settings.load())
    db = index.connect()
    try:
        plan = " ".join(str(r[3]) for r in db.execute("explain query plan select f.path, f.name from files f" + search.where(parts), values))
    finally:
        db.close()
    assert "files_size" not in plan, plan
