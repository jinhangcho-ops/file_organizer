"""여덟 번째 감사(색인·검색 쪽)에서 막은 것: 사진·동영상 짝 맞추기 동안 잡고 있던 쓰기 잠금, 클라우드 전용 문서의 받은 곳·편집한 사람,
종류별 개수·좁은 조건의 정렬·넓은 조건 판단·정렬만 바꿀 때의 중복 묶기 같은 조회 방식, 할 일 동영상끼리 두 번 견주기,
클라우드 전용만 남았을 때 인식 모델 올리기, 늘 나는 읽기 오류의 인식, 길이를 모르는 동영상의 할 일.
파일은 모두 지어낸다. 실제 클라우드 파일은 쓰지 않는다(클라우드 전용은 판별 함수를 바꿔 끼운다)."""
import os
import sqlite3
import threading
import types

import pytest

import extract
import index
import media
import models
import provenance
import recognize
import search
import settings
import tree
from conftest import build, write
from test_seventh_audit_b import docx, fake_videos, names, picture

STEPS_OFF = {"index_steps": [{"name": name, "on": False} for name in settings.STEPS]}


class Gate:
    """at번째로 불릴 때 busy를 켜고 go를 기다린 뒤 진짜 함수를 부른다(오래 읽거나 계산하는 동안처럼)."""

    def __init__(self, real, at=1):
        self.real, self.at, self.calls, self.busy, self.go = real, at, 0, threading.Event(), threading.Event()

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.at:
            self.busy.set()
            self.go.wait(30)
        return self.real(*args, **kwargs)


def others_can_write(gate, step):
    """step(db)을 따로 돌리고, gate에서 멈춘 동안 다른 연결(옮기기·휴지통의 index.moved/removed처럼)이 쓰기를 시작할 수 있는지."""
    errors = []

    def work():
        db = index.connect()
        try:
            step(db)
        except Exception as e:  # 검사가 본다
            errors.append(e)
        finally:
            db.close()

    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert gate.busy.wait(30), "멈출 곳까지 가지 않았습니다"
        other = sqlite3.connect(settings.folder() / "index.db", timeout=0.5)
        try:
            other.execute("begin immediate")
            other.rollback()
        finally:
            other.close()
    finally:
        gate.go.set()
        thread.join(60)
    assert not errors, errors


# B1 ─ 사진·동영상 짝 맞추기(오래 걸린다) 전에 마지막 지문과 사진 짝 맞추기의 할 일 지우기를 저장한다.
@pytest.mark.parametrize("where", ["match_images", "match_videos"])
def test_media_matching_does_not_hold_the_write_lock(home, monkeypatch, where):
    settings.save(STEPS_OFF)
    from PIL import Image
    Image.new("RGB", (40, 30), (90, 90, 90)).save(home / "한색.png")  # 한 가지 색: 지문 없음(할 일 없이 media 줄만 쓴다)
    build(home)
    db = index.connect()
    try:
        fake_videos(db, 4)
        db.execute("insert into media(id, kind, duration, frames, made, interval) values(9000, 'image', null, ?, 0, null)", (media.encode([12345]),))
        db.execute("insert into media_todo(id) values(9000)")  # 이미 지문을 만든 사진: match_images가 할 일을 지운다
        db.commit()
    finally:
        db.close()
    with monkeypatch.context() as patch:
        patch.setattr(index, "SAVE_EVERY", 10 ** 6)  # 시간으로는 저장하지 않게
        name = "decode" if where == "match_images" else "similarity"
        gate = Gate(getattr(media, name))
        patch.setattr(media, name, gate)
        others_can_write(gate, media.run)
    db = index.connect()
    try:
        assert db.execute("select count(*) from media_todo").fetchone()[0] == 0
        assert db.execute("select count(*) from media m join files f on f.id = m.id where f.name = '한색.png'").fetchone()[0] == 1
    finally:
        db.close()


# B2 ─ 클라우드 전용 문서는 받은 곳·편집한 사람을 보려고 열지 않는다(열면 받아 온다). 받은 곳·소유자는 그대로.
def test_provenance_does_not_open_cloud_only_documents(tmp_path, monkeypatch):
    def no_open(path):
        raise AssertionError(f"클라우드 전용 문서를 열었습니다: {path}")

    for name in ("문서.docx", "문서.pdf"):
        path = write(tmp_path / name, "가짜 문서")
        with open(path + ":Zone.Identifier", "w", encoding="utf-8") as stream:
            stream.write("[ZoneTransfer]\nZoneId=3\nHostUrl=https://example.com/a.docx?token=1\n")
        with monkeypatch.context() as patch:
            patch.setattr(index, "cloud_only", lambda st: True)
            patch.setattr(provenance, "office_people", no_open)
            patch.setattr(provenance, "pdf_author", no_open)
            found = provenance.info(path)
        assert found["source"] == "https://example.com/a.docx?…" and found["author"] == "" and found["owner"]
    assert provenance.info(docx(tmp_path / "진짜.docx"))["author"] is not None  # 이 PC에 있는 문서는 그대로 읽는다


# B3·B4 ─ 조회 방식: 실제로 보낸 문장(값이 채워진 것)을 trace로 받아 쿼리 계획을 본다.
def traced(monkeypatch, run):
    statements = []
    real = index.connect

    def connect():
        db = real()
        db.set_trace_callback(statements.append)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(index, "connect", connect)
        result = run()
    return result, statements


def plan(sql):
    db = index.connect()
    try:
        return " ".join(str(r[3]) for r in db.execute("explain query plan " + sql))
    finally:
        db.close()


def many_files(home):
    for n in range(12):
        write(home / "폴더" / f"글{n}.txt", "x")
    for n in range(3):
        picture(home / f"그림{n}.png")
    write(home / "큰.bin", "x" * (2 * 1024 * 1024))
    write(home / "작은" / "하나.txt", "x")
    build(home)


def test_kind_counts_do_not_walk_the_kind_index(home, monkeypatch):
    many_files(home)
    found, statements = traced(monkeypatch, lambda: (search.listing({"name": "글"}), tree.summary(str(home / "폴더"))))
    listing, summary = found
    assert dict(listing["kinds"])["문서"] == 12 and sum(n for _, n in listing["kinds"]) == 12
    assert summary["kinds"] == [["문서", 12]]
    grouped = [s for s in statements if "group by" in s and "f.kind" in s.split("group by")[-1]]
    assert len(grouped) == 2, statements
    for sql in grouped:
        assert "files_kind" not in plan(sql), (sql, plan(sql))
    everything = search.listing({})  # 조건이 없으면 종류 색인만 읽는다(결과는 같다)
    assert sum(n for _, n in everything["kinds"]) == everything["total"] == 17 and dict(everything["kinds"])["문서"] == 13


def test_narrow_condition_sorts_only_the_picked_rows(home, monkeypatch):
    many_files(home)
    for query, picked, used in (({"size_min": "1"}, {"큰.bin"}, "files_size (size>?)"),
                                ({"folder": str(home / "작은"), "sort": "size"}, {"하나.txt"}, "sqlite_autoindex_files_1 (key>? AND key<?)")):
        found, statements = traced(monkeypatch, lambda: search.listing(query | {"rows_only": True}))
        assert names(found["rows"]) == picked
        shown = plan(next(s for s in statements if f"limit {search.LIMIT + 1}" in s))  # 조건의 색인으로 고르고, 고른 줄만 따로 정렬한다
        assert used in shown and "TEMP B-TREE FOR ORDER BY" in shown, (query, shown)
    found, statements = traced(monkeypatch, lambda: search.listing({"rows_only": True}))  # 조건이 없으면 그대로 날짜 색인으로 앞의 몇 줄만
    shown = plan(next(s for s in statements if f"limit {search.LIMIT + 1}" in s))
    assert len(found["rows"]) == 17 and "SCAN f USING INDEX files_modified" in shown, shown


# B5 ─ 넓은 조건인지는 줄 수로 판단한다(큰 폴더를 지워 id가 듬성듬성해져도).
def test_wide_threshold_counts_rows_not_ids(home):
    for n in range(20):
        write(home / "큰" / f"{n}.txt")
    write(home / "작은" / "a.txt")
    gone = home / "지울"
    for n in range(300):
        write(gone / f"{n}.txt")
    build(home)
    for n in range(300):
        os.remove(gone / f"{n}.txt")
    os.rmdir(gone)
    build(home)
    db = index.connect()
    try:
        rows, top = db.execute("select count(*), max(rowid) from files").fetchone()
    finally:
        db.close()
    assert rows == 21 and top > 10 * rows  # 지운 폴더의 id가 남은 것보다 뒤에 있어 듬성듬성하다
    assert search.under(str(home / "큰"))[0].startswith("+f.key")  # 20/21: 넓다(훑는다)
    assert search.under(str(home / "작은"))[0].startswith("f.key")


# B6 ─ 정렬만 바꿀 때(rows_only)는 중복 의심을 모으지 않는다(중복 의심 목록을 볼 때만).
def test_rows_only_does_not_collect_duplicates(home, monkeypatch):
    write(home / "a.txt", "같은 내용")
    write(home / "b" / "a.txt", "같은 내용")
    write(home / "c.txt", "다른 내용!")
    build(home)
    found, statements = traced(monkeypatch, lambda: search.listing({"sort": "size", "rows_only": True}))
    assert len(found["rows"]) == 3 and not any("same_files select" in s for s in statements)
    found, statements = traced(monkeypatch, lambda: search.listing({"view": "duplicates", "rows_only": True}))
    assert len(found["rows"]) == 2 and any("same_files select" in s for s in statements)
    assert dict(search.listing({})["views"])["duplicates"] == 2


# B7 ─ 할 일인 동영상이 여럿이면, 할 일끼리는 한 번만 견준다(모두 다시 맞춘 것과 같은 짝).
def test_new_videos_compare_each_pair_once(home, monkeypatch):
    db = index.connect()
    try:
        fake_videos(db, 60)
        media.run(db)
        everything = set(db.execute("select a, b, score from media_pair").fetchall())
        videos = [(r["id"], r["duration"]) for r in db.execute("select id, duration from media where kind = 'video' order by duration, id")]
        new = {1000 + n for n in (3, 4, 20, 21, 22, 40, 59)}
        for i in new:
            db.execute("delete from media_pair where a = ? or b = ?", (i, i))
            db.execute("insert into media_todo(id) values(?)", (i,))
        expected = sum(1 for n, (a, la) in enumerate(videos) for b, lb in videos[n + 1:]
                       if lb <= la * (1 + media.LENGTH_GAP) and (a in new or b in new))
        calls = []
        real = media.similarity
        with monkeypatch.context() as patch:
            patch.setattr(media, "similarity", lambda a, b: calls.append(1) or real(a, b))
            media.run(db)
        assert set(db.execute("select a, b, score from media_pair").fetchall()) == everything
        assert len(calls) == expected
    finally:
        db.close()


# B8 ─ 클라우드 전용 사진만 남았으면 인식 모델을 올리지 않고, 기록도 남기지 않는다.
def test_recognize_does_not_load_the_model_for_cloud_only_files(home, monkeypatch):
    settings.save(STEPS_OFF)
    picture(home / "a.png")
    build(home)

    def no_engine(config):
        raise AssertionError("클라우드 전용만 남았는데 모델을 올렸습니다")

    reports = []
    with monkeypatch.context() as patch:
        patch.setattr(models, "ready", lambda name: True)
        patch.setattr(recognize, "Engine", no_engine)
        patch.setattr(index, "cloud_only", lambda st: True)
        db = index.connect()
        try:
            recognize.work(db, lambda **changes: reports.append(changes))
            assert db.execute("select count(*) from recognized").fetchone()[0] == 0
        finally:
            db.close()
    assert reports[0]["total"] == 0


# B9(a) ─ 인식도 사진을 읽다 늘 나는 오류(배드 섹터 등)는 빈 결과로 기록하고, 잠시 막힌 것만 다음에 다시 한다.
def test_recognize_permanent_read_error_is_not_retried(home, monkeypatch):
    settings.save(STEPS_OFF)
    picture(home / "a.png")
    build(home)
    db = index.connect()
    try:
        row = dict(db.execute("select id, path, ext, stamp from files where name = 'a.png'").fetchone())
        engine = types.SimpleNamespace(np=None, faces=None, custom=None)
        for error, again in ((OSError(0, "데이터 오류(CRC)", None, 23), False), (OSError(0, "잠김", None, 33), True),
                             (PermissionError(13, "Permission denied"), True)):  # 마지막: 번호가 빈 오류인데 다시 읽힌다(잠깐 막혔다)
            def fail(path, error=error):
                raise error

            db.execute("delete from recognized")
            with monkeypatch.context() as patch:
                patch.setattr(recognize, "open_image", fail)
                if again:
                    with pytest.raises(OSError):
                        recognize.recognize_one(db, engine, row, {}, None)
                else:
                    assert recognize.recognize_one(db, engine, row, {}, None)
            assert db.execute("select count(*) from recognized").fetchone()[0] == (0 if again else 1), error
    finally:
        db.close()


# B9(b) ─ 길이를 모르는 동영상의 할 일은 지운다(남으면 색인마다 동영상 짝 맞추기를 다시 돈다).
def test_video_with_unknown_length_does_not_keep_matching(home, monkeypatch):
    db = index.connect()
    try:
        fake_videos(db, 3)
        media.run(db)  # 처음: 모두 맞추고 기준을 남긴다
        db.execute("insert into media(id, kind, duration, frames, made, interval) values(8000, 'video', null, '', 0, 120)")
        db.execute("insert into media_todo(id) values(8000)")
        media.run(db)  # 새 동영상만 견준다(new_videos): 길이를 모르는 것은 견줄 수 없으니 할 일에서 뺀다
        assert db.execute("select count(*) from media_todo").fetchone()[0] == 0

        def no_match(db, similar):
            raise AssertionError("할 일이 없는데 동영상 짝 맞추기를 다시 했습니다")

        with monkeypatch.context() as patch:
            patch.setattr(media, "match_videos", no_match)
            media.run(db)
    finally:
        db.close()


# B10 ─ 단계마다: 파일을 읽거나 계산하는 동안(첫 파일부터) 다른 연결이 쓰기를 시작할 수 있다.
def ready_hash(home):
    write(home / "a.bin", "같은크기1")
    write(home / "b.bin", "같은크기2")
    return index, "fingerprint", index.fingerprints, "update files set hashed = null"


def ready_text(home):
    write(home / "a.docx", "")  # 문서 글자 지문을 채우는 일(fill_text_hashes)도 함께 돈다
    write(home / "b.txt", "둘째 글자")
    return extract, "text", index.read_texts, "update files set textstamp = null, texthash = null"


def ready_doc(home):
    docx(home / "a.docx")
    docx(home / "b.docx", ["다른 글자"])
    return index, "doc_fingerprint", index.documents, "update files set dochashed = null"


def ready_media(home):
    picture(home / "a.png")
    picture(home / "b.png", (10, 10, 200))
    return media, "image_hash", media.run, "delete from media"


def ready_videos(home):
    def reset(db):
        db.execute("delete from meta")
        fake_videos(db, 4)
    return media, "similarity", media.run, reset


@pytest.mark.parametrize("ready", [ready_hash, ready_text, ready_doc, ready_media, ready_videos])
@pytest.mark.parametrize("at", [1, 2])
def test_steps_let_others_write_while_busy(home, monkeypatch, ready, at):
    settings.save(STEPS_OFF)
    module, name, step, reset = ready(home)
    build(home)
    db = index.connect()
    try:
        reset(db) if callable(reset) else db.execute(reset)
        db.commit()
    finally:
        db.close()
    with monkeypatch.context() as patch:
        patch.setattr(index, "SAVE_EVERY", 10 ** 6)
        patch.setattr(index, "RELEASE_AT", 0)  # 지어낸 파일은 작으니 큰 파일처럼(읽기 전에 저장)
        gate = Gate(getattr(module, name), at)
        patch.setattr(module, name, gate)
        others_can_write(gate, step)
