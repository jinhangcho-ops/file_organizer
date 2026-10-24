"""다섯 번째 감사에서 막은 것: 이모지가 든 긴 경로·휴지통 안 경로·Windows가 다룰 수 없는 이름, 쓸 수 없는 글자가 든 옮길 곳,
날짜가 깨진 파일, LZMA로 압축된 깨진 문서, 읽지 못한 색인 폴더, 잠시 잠긴 사진, 멈춘 동영상 짝 맞추기. 파일은 모두 지어낸다.
실제 휴지통은 쓰지 않는다(trash.problem만 부른다)."""
import ctypes
import os
import subprocess
import zipfile

import pytest

import actions
import index
import lock
import media
import provenance
import settings
import trash
from conftest import build, write


def test_trash_counts_paths_like_windows_and_refuses_odd_names(tmp_path, monkeypatch):
    folder = tmp_path / "이모지"
    target = folder / ("😀" * 100 + ".txt")  # 이름 하나는 204단위(255 아래). 경로는 파이썬 글자 수로 260 아래, Windows(UTF-16)로 넘는다
    write(target)
    assert len(str(target)) < 260 <= trash.width(str(target)), (len(str(target)), trash.width(str(target)))
    assert "너무 길어" in trash.problem(str(folder)) and "너무 길어" in trash.problem(str(target))
    shallow = tmp_path / "얕은"  # 폴더는 휴지통 안에서 원래 폴더 경로 대신 앞부분(BIN_PREFIX)이 붙는다
    write(shallow / ("가" * 40) / "a.txt")
    assert trash.problem(str(shallow)) is None
    monkeypatch.setattr(trash, "BIN_PREFIX", 260 - 40)  # 시험 폴더 경로가 이미 길어 앞부분을 키워 본다(안쪽 상대 경로 46자)
    assert "너무 길어" in trash.problem(str(shallow))
    assert trash.problem(str(shallow / ("가" * 40) / "a.txt")) is None  # 파일 하나는 휴지통 맨 위에 $R…로 들어간다
    odd = tmp_path / "abc."
    write(tmp_path / "abc")  # 같은 폴더에 abc도 있다: Windows는 'abc.'를 abc로 읽는다
    open("\\\\?\\" + str(odd), "w").close()  # 끝의 점은 \\?\로만 만들 수 있다
    try:
        assert "다룰 수 없는" in trash.problem(str(odd))
    finally:
        os.remove("\\\\?\\" + str(odd))


def test_unstorable_destination_and_name_are_refused(tmp_path):
    bad = tmp_path / "bad\udc80"
    bad.mkdir()
    with pytest.raises(ValueError, match="쓸 수 없는 글자"):
        settings.folder_text(str(bad), "옮길 곳")
    with pytest.raises(ValueError, match="쓸 수 없는 이름"):
        actions.check_name("새\udc80이름")


def test_broken_dates_and_lzma_documents_do_not_stop_the_index(home, tmp_path):
    old = write(home / "1601년.txt", "날짜가 깨진 파일입니다 " * 3)
    os.utime(old, ns=(0, -11644473600 * 10 ** 9 + 100))  # FILETIME 1 근처: 나노초가 64비트 정수 밖
    doc = tmp_path / "깨진.docx"
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_LZMA) as z:
        z.writestr("word/document.xml", os.urandom(4000).hex())
        z.writestr("docProps/core.xml", os.urandom(4000).hex())
    data = bytearray(doc.read_bytes())
    for i in range(120, 600):
        data[i] ^= 0x5A
    doc.write_bytes(bytes(data))
    assert index.doc_fingerprint(str(doc)) == "" and provenance.office_people(str(doc)) == ("", "")
    write(home / "보통.txt")
    build(home)
    db = index.connect()
    try:
        assert sorted(r[0] for r in db.execute("select name from files")) == ["1601년.txt", "보통.txt"]
    finally:
        db.close()


def test_unreadable_folder_keeps_its_records(home, unlock_after):
    write(home / "가" / "a.txt", "읽지 못하게 될 폴더의 파일입니다 " * 3)
    write(home / "나.txt")
    build(home)
    sid = lock.user_sid()
    subprocess.run(["icacls", str(home / "가"), "/deny", f"*{sid}:(RD)"], check=True, capture_output=True)  # 목록 읽기를 막는다
    try:
        build(home)
    finally:
        subprocess.run(["icacls", str(home / "가"), "/remove:d", f"*{sid}"], capture_output=True)
    db = index.connect()
    try:
        assert db.execute("select count(*) from files where name = 'a.txt'").fetchone()[0] == 1  # 지우지 않는다
    finally:
        db.close()


def test_locked_picture_is_tried_again_later(home):
    picture = home / "잠긴.png"
    from PIL import Image
    img = Image.new("RGB", (40, 30), (10, 200, 10))
    img.paste((200, 10, 10), (0, 0, 20, 30))
    img.save(picture)
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = '잠긴.png'").fetchone())
        db.execute("delete from media where id = ?", (row["id"],))
        handle = ctypes.windll.kernel32.CreateFileW(str(picture), 0x80000000, 0, None, 3, 0x80, None)  # 공유 없이 연다
        try:
            with pytest.raises(OSError):
                media.fingerprint(db, row, 30)
            assert db.execute("select count(*) from media where id = ?", (row["id"],)).fetchone()[0] == 0  # 기록하지 않는다
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        media.fingerprint(db, row, 30)
        assert db.execute("select count(*) from media where id = ?", (row["id"],)).fetchone()[0] == 1
    finally:
        db.close()


def test_stopped_video_matching_keeps_old_pairs(monkeypatch):
    db = index.connect()
    try:
        frames = media.encode([1, 2, 3, 4, 5, 6])
        for i in (901, 902, 903):
            db.execute("insert or replace into media(id, kind, duration, frames, made, interval) values(?, 'video', 30, ?, 0, 30)", (i, frames))
        db.execute("insert or replace into media_pair(a, b, score) values(901, 902, 99)")

        def stop_midway(a, b):
            index.stop.set()
            return None  # 멈추며 이번에는 짝이 아니라고 한다: 있던 짝이 지워졌다면 다시 생기지 않는다

        monkeypatch.setattr(media, "similarity", stop_midway)
        media.match_videos(db, 80)
        assert db.execute("select count(*) from media_pair where a = 901 and b = 902").fetchone()[0] == 1  # 있던 짝은 그대로
    finally:
        index.stop.clear()
        db.close()
