"""아홉 번째 감사(B)에서 막은 것: 정션으로 들어간 색인 폴더 아래의 차단 폴더, 사진·동영상 짝 맞추기가 오래 잡던 쓰기 잠금,
좁은 조건에서 종류 색인을 따라가던 목록, 사라진 파일을 한 번에 지우던 색인, 인식 전에 모든 파일을 stat하던 것,
옮긴 파일 하나가 사라지면 모두 되돌아가던 moved, 목록 한 번에 열 번씩 세던 전체 줄 수. 파일·색인은 모두 지어낸다."""
import os
import random
import sqlite3
import subprocess
import time
import types

import pytest

import exclude
import fence
import index
import kinds
import media
import recognize
import search
import settings
from conftest import build, write


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def keys(where="1"):
    db = index.connect()
    try:
        return {row[0] for row in db.execute(f"select key from files where {where}")}
    finally:
        db.close()


def test_blocked_folder_under_junction_root_is_not_indexed(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    write(target / "비밀" / "숨김.txt")
    write(target / "보통.txt")
    junction(link, target)
    try:
        settings.save({"roots": [str(home), str(link)]})
        index.stop.clear()
        index.build([os.path.abspath(link)])  # 예전처럼 정션 경로로 색인해 둔 줄
        assert exclude.key(link / "보통.txt") in keys()
        with monkeypatch.context() as m:
            m.setattr(fence, "keys", lambda: {exclude.key(target / "비밀")})
            build(home)
            found = keys()
            assert not any("비밀" in k for k in found)  # 정션 경로로도 실제 경로로도 들어가지 않는다
            assert not any(k.startswith(exclude.key(link) + os.sep) for k in found)  # 정션 경로의 옛 줄은 사라졌다
            assert exclude.key(target / "보통.txt") in found
            with pytest.raises(ValueError, match="차단"):
                fence.refuse(str(target / "비밀" / "숨김.txt"))
        # 놓을 곳·정리처럼 정션 경로로 알려 온 옮기기도 색인에 남는다(실제 경로로)
        os.rename(target / "보통.txt", target / "새.txt")
        index.moved([(str(link / "보통.txt"), str(link / "새.txt"))])
        found = keys()
        assert exclude.key(target / "새.txt") in found and exclude.key(target / "보통.txt") not in found
    finally:
        os.rmdir(link)


class Probe:
    """index.stop 대신: at번째로 물을 때 다른 연결로 써 본다(index.moved처럼, 짧게 기다린다). stop_at번째부터 멈춘다."""

    def __init__(self, at, stop_at=None):
        self.n, self.at, self.stop_at, self.result = 0, at, stop_at, None

    def is_set(self):
        self.n += 1
        if self.n == self.at:
            other = sqlite3.connect(settings.folder() / "index.db", timeout=0.2)
            try:
                other.execute("insert or replace into meta(name, value) values('probe', 1)")
                other.commit()
                self.result = "ok"
            except sqlite3.OperationalError as e:
                self.result = str(e)
            finally:
                other.close()
        return self.stop_at is not None and self.n >= self.stop_at


def photos(db, n=100):
    """사진 2n장: 앞 n장은 아무 지문, 뒤 n장은 앞의 것과 1비트만 다르다. 맞는 짝(지문으로 모두 견줘 구한 것)을 돌려준다."""
    random.seed(9)
    hashes = {i: random.getrandbits(64) for i in range(1, n + 1)}
    hashes |= {i + n: hashes[i] ^ (1 << i % 64) for i in range(1, n + 1)}
    db.executemany("insert into media(id, kind, duration, frames, made, interval) values(?, 'image', null, ?, 0, null)",
                   [(i, media.encode([h])) for i, h in hashes.items()])
    db.executemany("insert into media_todo(id) values(?)", [(i,) for i in hashes])
    db.commit()
    return {(a, b, round(100 * (1 - media.distance(hashes[a], hashes[b]) / 64))) for a in hashes for b in hashes
            if a < b and media.distance(hashes[a], hashes[b]) <= media.IMAGE_BITS}


def pairs(db):
    return {tuple(row) for row in db.execute("select a, b, score from media_pair")}


def test_matching_photos_lets_other_writers_in(home, monkeypatch):
    db = index.connect()
    try:
        expected = photos(db)
        probe = Probe(50)
        with monkeypatch.context() as m:
            m.setattr(index, "stop", probe)
            index.saved[0] = time.time()  # 2초 안: 예전에는 그때까지 쓰기 잠금을 쥐고 있었다
            media.match_images(db)
        db.commit()
        assert probe.result == "ok"
        assert pairs(db) == expected and len(expected) == 100
        assert db.execute("select count(*) from media_todo").fetchone()[0] == 0
    finally:
        db.close()


def test_matching_photos_stops_and_keeps_what_it_did(home, monkeypatch):
    db = index.connect()
    try:
        photos(db)
        with monkeypatch.context() as m:
            m.setattr(index, "stop", Probe(0, stop_at=51))
            media.match_images(db)
        assert db.execute("select count(*) from media_todo").fetchone()[0] == 150  # 50장을 했다
        assert pairs(db) == {(i, i + 100, 98) for i in range(1, 51)}
        media.match_images(db)  # 이어서 하면 모두
        assert len(pairs(db)) == 100
    finally:
        db.close()


def test_matching_new_videos_lets_other_writers_in(home, monkeypatch):
    random.seed(4)
    db = index.connect()
    try:
        rows = []
        for j in range(15):  # 길이가 서로 멀고(1.5배씩), 같은 길이·같은 장면인 동영상이 둘씩
            frames = media.encode([random.getrandbits(64) for _ in range(5)])
            rows += [(2 * j + 1, 100 * 1.5 ** j, frames), (2 * j + 2, 100 * 1.5 ** j, frames)]
        db.executemany("insert into media(id, kind, duration, frames, made, interval) values(?, 'video', ?, ?, 0, 120)", rows)
        db.executemany("insert into media_todo(id) values(?)", [(r[0],) for r in rows])
        db.execute("insert into meta(name, value) values('video_similar', 80)")
        db.commit()
        probe = Probe(5)
        with monkeypatch.context() as m:
            m.setattr(index, "stop", probe)
            index.saved[0] = time.time()
            media.match_videos(db, 80)
        db.commit()
        assert probe.result == "ok"
        assert pairs(db) == {(2 * j + 1, 2 * j + 2, 100) for j in range(15)}
        assert db.execute("select count(*) from media_todo").fetchone()[0] == 0
    finally:
        db.close()


def traced(m):
    """index.connect가 여는 연결마다 실행한 SQL(값을 채운 것)을 모은다."""
    statements, real = [], index.connect

    def connect():
        db = real()
        db.set_trace_callback(statements.append)
        return db

    m.setattr(index, "connect", connect)
    return statements


def test_narrow_folder_with_kind_does_not_walk_kind_index(home, monkeypatch):
    for n in range(40):
        write(home / "큰" / f"{n}.txt")
        write(home / "큰" / f"{n}.png")
    write(home / "작은" / "가.txt")
    write(home / "작은" / "나.txt")
    build(home)
    with monkeypatch.context() as m:
        statements = traced(m)
        found = search.listing({"folder": str(home / "작은"), "kind": kinds.of(".txt")})
    assert sorted(r["name"] for r in found["rows"]) == ["가.txt", "나.txt"] and found["total"] == 2
    db = index.connect()
    try:
        search.same_files(db)
        plans = {s: " | ".join(row[3] for row in db.execute("explain query plan " + s))
                 for s in statements if "f.kind = " in s and s.lstrip().lower().startswith("select")}
    finally:
        db.close()
    assert plans and not [s for s, plan in plans.items() if "files_kind" in plan], plans


def test_gone_files_are_removed_in_saved_chunks_and_stop(home, tmp_path, monkeypatch):
    for n in range(6):
        write(home / "지울" / f"{n}.txt")
    write(home / "남김.txt")
    build(home)
    os.rename(home / "지울", tmp_path / "밖")  # 색인한 뒤에 사라졌다
    real = index.drop_ids

    def drop_then_stop(db, ids):
        real(db, ids)
        index.stop.set()  # 첫 묶음을 지운 뒤 창을 닫았다

    with monkeypatch.context() as m:
        m.setattr(index, "GONE_CHUNK", 2)
        m.setattr(index, "drop_ids", drop_then_stop)
        index.stop.clear()
        index.build(index.roots(settings.load()))
    other = sqlite3.connect(settings.folder() / "index.db")
    try:  # 다른 연결에서 보인다: 지운 묶음은 저장했다
        assert other.execute("select count(*) from files where name like '%.txt' and name <> '남김.txt'").fetchone()[0] == 4
        assert other.execute("select count(*) from folders where key = ?", (exclude.key(home / "지울"),)).fetchone()[0] == 1
    finally:
        other.close()
    build(home)  # 다음 색인이 나머지를 지운다
    assert keys() == {exclude.key(home / "남김.txt")}


def test_recognize_does_not_stat_every_file_before_loading(home, monkeypatch):
    for n in range(5):
        write(home / f"{n}.png", "그림 아님")
    build(home)
    calls, done = [], []
    with monkeypatch.context() as m:
        m.setattr(recognize.models, "ready", lambda name: name == "clip")
        m.setattr(recognize, "Engine", lambda config: types.SimpleNamespace(faces=None, close=lambda: None))
        m.setattr(recognize, "local", lambda path: calls.append(path) or True)
        m.setattr(recognize, "recognize_one", lambda db, engine, row, config, centers: done.append(row["path"]))
        db = index.connect()
        try:
            recognize.work(db, lambda **changes: None)
        finally:
            db.close()
    assert len(calls) == 1 and len(done) == 5


def test_recognize_skips_files_that_are_not_here(home, monkeypatch):
    write(home / "가.png", "그림 아님")
    build(home)
    db = index.connect()
    try:
        row = db.execute("select id, path, ext, stamp from files").fetchone()
        engine = types.SimpleNamespace(np=None)
        with monkeypatch.context() as m:
            m.setattr(index, "cloud_only", lambda st: True)
            assert recognize.recognize_one(db, engine, row, {}, None) is False
        with monkeypatch.context() as m:  # 하나도 이 PC에 없으면 모델을 올리지 않는다
            m.setattr(recognize.models, "ready", lambda name: name == "clip")
            m.setattr(recognize, "Engine", lambda config: pytest.fail("모델을 올렸습니다"))
            m.setattr(recognize, "local", lambda path: False)
            recognize.work(db, lambda **changes: None)
        os.rename(row["path"], home / "다른.bin")
        with pytest.raises(OSError):  # 사라진 파일: work가 건너뛴다
            recognize.recognize_one(db, engine, row, {}, None)
    finally:
        db.close()


def test_moved_keeps_other_files_when_one_vanished(home):
    write(home / "가.txt")
    build(home)
    os.rename(home / "가.txt", home / "나.txt")
    index.moved([(str(home / "가.txt"), str(home / "나.txt")), (str(home / "밖.txt"), str(home / "사라짐.txt"))])
    assert keys() == {exclude.key(home / "나.txt")}


def test_listing_counts_all_files_once(home, monkeypatch):
    for n in range(20):
        write(home / "폴더" / f"{n}.txt", "가" * n)
    build(home)
    with monkeypatch.context() as m:
        statements = traced(m)
        found = search.listing({"folder": str(home / "폴더"), "size_min": "0"})
    assert found["total"] == 20
    assert [s.strip() for s in statements].count("select count(*) from files") == 1
