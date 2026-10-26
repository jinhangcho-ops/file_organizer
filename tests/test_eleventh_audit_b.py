"""열한 번째 감사(색인 쪽): subst 드라이브는 실제 경로로(옛 판의 줄·차단 기록이 맞게), 읽는 동안 휴지통으로 보낸 파일의
기록이 홀로 남지 않게, 옛 색인 맞추기(migrate)의 멈춤·저장·단계, 드라이브 루트를 가리키는 정션, 다른 폴더 안으로 옮겨 간 맨 위 폴더.
파일·색인은 모두 지어낸다(subst 드라이브는 realpath만 바꿔 흉내 낸다)."""
import os
import types

import exclude
import extract
import index
import media
import recognize
import settings
from conftest import build, write
from test_tenth_audit_b import STEPS_OFF, junction, keys, literal_roots, old_build, rows


def fake_subst(m, drive, target):
    """drive(S:)가 target을 가리키는 subst 드라이브인 것처럼 realpath·isdir을 바꾼다."""
    plain_real, plain_isdir = os.path.realpath, os.path.isdir

    def to_target(path):
        return str(target) + "\\" + str(path)[3:] if str(path).upper().startswith(drive + "\\") else str(path)
    m.setattr(os.path, "realpath", lambda path, **kw: plain_real(to_target(path), **kw))
    m.setattr(os.path, "isdir", lambda path: plain_isdir(to_target(path)))


# B1 ─ subst 드라이브는 실제 경로로 바꾼다: 옛 판(9차)이 실제 경로로 넣은 줄과 차단 기록이 그대로 맞는다. 네트워크 드라이브만 글자 그대로.
def test_subst_drive_root_keeps_rows_of_previous_version(home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    write(work / "문서" / "a.txt", "가" * 30)
    literal_roots(home, work / "문서")  # 옛 판은 S:\문서를 실제 경로로 바꿔 넣었다
    build(home)
    file_id = rows("select id from files where key = ?", (exclude.key(work / "문서" / "a.txt"),))[0][0]
    db = index.connect()
    db.execute("insert into recognized(id, made, faces) values(?, 1, 0)", (file_id,))
    db.commit()
    db.close()
    literal_roots(home, "S:\\문서")
    with monkeypatch.context() as m:
        fake_subst(m, "S:", work)
        assert [exclude.key(p) for p in index.roots(settings.load())] == [exclude.key(home), exclude.key(work / "문서")]
        build(home)
    assert exclude.key(work / "문서" / "a.txt") in keys()
    assert rows("select id from recognized") == [(file_id,)]


# B2 ─ 읽는 동안 다른 연결이 휴지통 반영(index.removed)을 하면 지문·인식·글자를 넣지 않는다.
def test_media_fingerprint_of_trashed_photo_is_not_kept(home, monkeypatch):
    paths = [write(home / f"p{i}.jpg", "그림 아님" * (i + 1)) for i in range(2)]
    settings.save(STEPS_OFF)
    build(home)
    db = index.connect()
    ids = [db.execute("select id from files where key = ?", (exclude.key(p),)).fetchone()[0] for p in paths]
    h = 0x0f0f0f0f0f0f0f0f
    db.execute("insert into media(id, kind, frames, made) values(?, 'image', ?, 0)", (ids[0], media.encode([h])))
    db.commit()
    row = db.execute("select id, path, ext, stamp from files where id = ?", (ids[1],)).fetchone()

    def read_then_trashed(path):
        index.removed([paths[1]])
        return h
    try:
        with monkeypatch.context() as m:
            m.setattr(media, "image_hash", read_then_trashed)
            m.setattr(media, "openable", lambda p: True)
            media.fingerprint(db, row, 120)
            db.commit()
            media.match_images(db)
            db.commit()
        assert [r[0] for r in db.execute("select id from media")] == [ids[0]]
        assert db.execute("select count(*) from media_pair").fetchone()[0] == 0
    finally:
        db.close()


def test_recognition_of_trashed_photo_is_not_kept(home, monkeypatch):
    path = write(home / "사진.png", "그림 아님")
    settings.save(STEPS_OFF)
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files").fetchone())

        def opened_then_trashed(p):
            index.removed([path])
            return False  # 열 수 없는 파일: 빈 결과로 표시하는 길
        with monkeypatch.context() as m:
            m.setattr(media, "openable", opened_then_trashed)
            recognize.recognize_one(db, types.SimpleNamespace(np=None, faces=None, custom=None), row, {}, None)
        db.commit()
        assert db.execute("select count(*) from recognized").fetchone()[0] == 0
        assert db.execute("select count(*) from tags").fetchone()[0] == 0
    finally:
        db.close()


def test_text_of_trashed_file_is_not_kept(home, monkeypatch):
    path = write(home / "글.txt", "가나다" * 10)
    settings.save(STEPS_OFF)
    build(home)
    db = index.connect()
    try:
        file_id = db.execute("select id from files").fetchone()[0]

        def read_then_trashed(p, ext):
            index.removed([path])
            return "가나다" * 10
        with monkeypatch.context() as m:
            m.setattr(extract, "text", read_then_trashed)
            index.read_text(db, file_id, path, ".txt", os.stat(path))
        db.commit()
        assert db.execute("select count(*) from body").fetchone()[0] == 0
    finally:
        db.close()


# B3 ─ 옛 색인 맞추기는 단계 이름을 보이고, 조금씩 저장하며, 멈추면 남은 옛 줄을 지우지 않고 다음 색인이 이어서 옮긴다.
def test_migrate_stops_and_resumes_without_losing_rows(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    for name in ("a.txt", "하위/b.txt", "하위2/c.txt"):
        write(target / name, "가" * 30)
    junction(link, target)
    try:
        literal_roots(home, link)
        old_build(monkeypatch, [home, link])
        before = dict(rows("select path, id from files"))
        steps, plain = [], index.save_now

        def save_then_stop(db, force=False):
            steps.append(index.status["step"])
            plain(db, True)
            index.stop.set()
        with monkeypatch.context() as m:
            m.setattr(index, "GONE_CHUNK", 1)
            m.setattr(index, "save_now", save_then_stop)
            build(home)
        assert steps[0] == "옛 색인 맞추기"
        half = dict(rows("select id, key from files"))
        assert sorted(half) == sorted(before.values())  # 멈춰도 지운 줄이 없다
        moved = [k for k in half.values() if k.startswith(exclude.key(target))]
        assert 0 < len(moved) < 3
        assert rows("select parent from folders where key = ?", (exclude.key(link),)) == [(None,)]  # 맨 위 줄은 남은 것과 함께 다음에
        build(home)
        now = dict(rows("select key, id from files"))
        assert sorted(now.values()) == sorted(before.values())
        assert sorted(now) == sorted(exclude.key(target / n) for n in ("a.txt", "하위/b.txt", "하위2/c.txt"))
        assert index.is_root(str(target))  # 다른 색인 폴더 안이 아니면 맨 위 폴더로 남는다
    finally:
        os.rmdir(link)


# B4 ─ 드라이브 루트를 가리키는 정션(J → C:\): 경로에 구분자가 겹치지 않고 폴더 이름이 비지 않는다(색인은 옮기기만, 디스크는 훑지 않는다).
def test_migrate_junction_to_drive_root_joins_paths(home, tmp_path, monkeypatch):
    link = tmp_path / "J"
    db = index.connect()
    try:
        index.add_folder(db, str(link), None)
        index.add_folder(db, str(link / "zz"), str(link))
        db.execute("insert into files(path, key, folder, name) values(?,?,?,?)",
                   (str(link / "zz" / "a.txt"), exclude.key(link / "zz" / "a.txt"), exclude.key(link / "zz"), "a.txt"))
        with monkeypatch.context() as m:
            m.setattr(index, "real", lambda p: "C:\\" if exclude.key(p) == exclude.key(link) else os.path.abspath(p))
            index.migrate(db)
        assert sorted(tuple(r) for r in db.execute("select key, path, parent, name from folders")) == [
            ("c:\\", "C:\\", None, "C:\\"), ("c:\\zz", "C:\\zz", "c:\\", "zz")]
        assert [tuple(r) for r in db.execute("select path, folder from files")] == [("C:\\zz\\a.txt", "c:\\zz")]
    finally:
        db.rollback()
        db.close()


# B5 ─ 정션이 다른 색인 폴더 안을 가리키면(연결 → 다른\x\T), 옮긴 줄은 그 폴더 아래에 붙는다(한 단계만 돌려도 맨 위 폴더가 되지 않게).
def test_migrated_top_inside_other_root_gets_parent_in_only_build(home, tmp_path, monkeypatch):
    other = tmp_path / "다른"
    target, link = other / "x" / "T", tmp_path / "연결"
    write(other / "r.txt")
    write(target / "a.txt", "가" * 30)
    junction(link, target)
    try:
        literal_roots(other, link)
        old_build(monkeypatch, [other, link])
        db = index.connect()
        index.forget(db, str(other / "x"))  # 다른\x 아래 줄이 없는 색인(정션 쪽으로만 넣었다)
        db.commit()
        db.close()
        file_id = rows("select id from files where key = ?", (exclude.key(link / "a.txt"),))[0][0]
        index.stop.clear()
        index.build(index.roots(settings.load()), only="hash")
        assert rows("select id from files where key = ?", (exclude.key(target / "a.txt"),)) == [(file_id,)]
        assert rows("select parent from folders where key = ?", (exclude.key(target),)) == [(exclude.key(other / "x"),)]
        assert rows("select parent from folders where key = ?", (exclude.key(other / "x"),)) == [(exclude.key(other),)]
        assert not index.is_root(str(target))
    finally:
        os.rmdir(link)


# B6 ─ 옮기기 반영(moved)은 exclude.real로 실제 경로를 찾고, 한 번의 호출 안에서 폴더마다 한 번만 찾는다.
def test_moved_finds_real_folder_once_per_folder(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    names = [f"{i}.txt" for i in range(3)]
    for name in names:
        write(home / "보낼곳" / name, name * 10)
    target.mkdir()
    junction(link, target)
    try:
        settings.save(STEPS_OFF | {"roots": [str(home), str(target)]})
        build(home)
        for name in names:
            os.rename(home / "보낼곳" / name, target / name)
        calls, plain = [], index.real
        with monkeypatch.context() as m:
            m.setattr(index, "real", lambda p: calls.append(p) or plain(p))
            index.moved([(str(home / "보낼곳" / n), str(link / n)) for n in names])
        assert len(calls) == 2
        assert {exclude.key(target / n) for n in names} <= keys()
    finally:
        os.rmdir(link)
