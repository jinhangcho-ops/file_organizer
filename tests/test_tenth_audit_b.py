"""열 번째 감사(B)에서 막은 것: 차단 폴더를 가리키는 정션 폴더, 정션의 대상(외장 디스크)을 뺐을 때 지워지던 기록,
업데이트 전 정션 경로로 넣은 줄(실제 경로로 옮긴다), 끊긴 첫 색인 뒤 같은 파일이 두 번 보이던 것, subst 드라이브,
옮기기마다 다시 하던 실제 경로 찾기, 짝 맞추는 중 지운 사진의 짝, 폴더 줄 지우기와 멈출 때 동영상 짝 쓰기. 파일·색인은 모두 지어낸다."""
import os
import subprocess
import time

import exclude
import fence
import index
import media
import search
import settings
from conftest import build, write

STEPS_OFF = {"index_steps": [{"name": name, "on": False} for name in settings.STEPS]}


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def rows(sql, values=()):
    db = index.connect()
    try:
        return [tuple(row) for row in db.execute(sql, values)]
    finally:
        db.close()


def keys():
    return {k for (k,) in rows("select key from files")}


def literal_roots(*paths):
    """업데이트 전에 저장한 설정처럼 폴더를 글자 그대로(정션 경로로) 둔다. settings.save는 이제 실제 경로로 바꿔 저장한다."""
    settings.write("settings.json", {**settings.read("settings.json", {}), "roots": [str(p) for p in paths], **STEPS_OFF})


def old_build(monkeypatch, paths):
    """업데이트 전처럼 정션을 따라가지 않고(글자 그대로) 색인한다."""
    with monkeypatch.context() as m:
        m.setattr(index, "real", os.path.abspath)
        index.stop.clear()
        index.build([os.path.abspath(p) for p in paths])


def test_root_junction_to_blocked_folder_is_skipped(home, tmp_path, monkeypatch):
    target, link = tmp_path / "비공개", tmp_path / "연결"
    write(target / "s.txt")
    junction(link, target)
    try:
        literal_roots(home, link)
        with monkeypatch.context() as m:
            m.setattr(fence, "keys", lambda: {exclude.key(target)})  # 차단은 실제 경로로 넣었다
            assert exclude.key(target) not in {exclude.key(p) for p in index.roots(settings.load())}
            build(home)
            assert not any(k.startswith(exclude.key(target)) or k.startswith(exclude.key(link)) for k in keys())
    finally:
        os.rmdir(link)


def test_junction_target_unplugged_keeps_rows(home, tmp_path):
    target, link = tmp_path / "외장", tmp_path / "연결"
    write(target / "a.txt")
    junction(link, target)
    try:
        literal_roots(home, link)
        build(home)
        db = index.connect()
        file_id = db.execute("select id from files where key = ?", (exclude.key(target / "a.txt"),)).fetchone()[0]
        db.execute("insert into recognized(id, made, faces) values(?, 1, 0)", (file_id,))
        db.commit()
        db.close()
        os.rename(target, tmp_path / "뺀 디스크")  # 디스크를 뺀 것처럼(정션은 대상을 잃는다)
        assert not os.path.isdir(link)
        build(home)
        assert exclude.key(target / "a.txt") in keys()
        assert rows("select id from recognized") == [(file_id,)]
    finally:
        os.rmdir(link)


def test_substituted_drive_resolves_and_network_drive_keeps_letter(home, monkeypatch, tmp_path):
    """subst S:는 실제 경로로 바꾸고(옛 판의 줄·차단 기록과 맞게), 네트워크 Z:는 드라이브 글자 그대로 둔다(드라이브로 고르기가 맞게)."""
    plain = os.path.realpath
    elsewhere = {"S:\\": str(tmp_path) + "\\", "Z:\\": "\\\\srv\\share\\"}

    def fake(path, **kw):
        for drive, to in elsewhere.items():
            if path.upper().startswith(drive):
                return to + path[3:]
        return plain(path, **kw)

    with monkeypatch.context() as m:
        m.setattr(os.path, "realpath", fake)
        assert index.real("S:\\문서\\a") == str(tmp_path / "문서" / "a")
        m.setattr(os.path, "isdir", lambda p: True)
        assert index.roots({"roots": ["Z:\\공유", "S:\\문서"], "dest": ""}) == ["Z:\\공유", str(tmp_path / "문서")]


def test_old_junction_rows_are_moved_to_real_path_with_ids(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    write(target / "a.txt", "가" * 30)
    write(target / "하위" / "b.txt", "나" * 31)
    junction(link, target)
    try:
        literal_roots(home, link)
        old_build(monkeypatch, [home, link])
        db = index.connect()
        old = dict(db.execute("select key, id from files"))
        a, b = old[exclude.key(link / "a.txt")], old[exclude.key(link / "하위" / "b.txt")]
        db.execute("insert into recognized(id, made, faces) values(?, 1, 1)", (a,))
        db.execute("insert into faces(id, n, person) values(?, 0, 1)", (a,))
        db.execute("insert into media(id, kind, frames, made) values(?, 'image', '', 0), (?, 'image', '', 0)", (a, b))
        db.execute("insert into media_pair(a, b, score) values(?, ?, 99)", (a, b))
        db.commit()
        db.close()
        build(home)
        now = dict(rows("select key, id from files"))
        assert now[exclude.key(target / "a.txt")] == a and now[exclude.key(target / "하위" / "b.txt")] == b
        assert not any(k.startswith(exclude.key(link)) for k in now)
        assert rows("select id from recognized") == [(a,)] and rows("select id from faces") == [(a,)]
        assert rows("select a, b from media_pair") == [(a, b)]
        folders = dict(rows("select key, parent from folders"))
        assert not any(k.startswith(exclude.key(link)) for k in folders)
        assert folders[exclude.key(target)] is None and index.is_root(str(target))  # 맨 위 폴더로 남는다
        assert folders[exclude.key(target / "하위")] == exclude.key(target)
    finally:
        os.rmdir(link)


def test_old_junction_row_and_real_row_for_same_file_keep_real(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    write(target / "a.txt")
    junction(link, target)
    try:
        literal_roots(home, link)
        old_build(monkeypatch, [home, link])
        db = index.connect()
        old_id = db.execute("select id from files where key = ?", (exclude.key(link / "a.txt"),)).fetchone()[0]
        db.execute("insert into recognized(id, made, faces) values(?, 1, 0)", (old_id,))
        index.save_file(db, str(target / "a.txt"), os.stat(target / "a.txt"))  # 같은 파일의 실제 경로 줄(끊긴 첫 색인 등)
        real_id = db.execute("select id from files where key = ?", (exclude.key(target / "a.txt"),)).fetchone()[0]
        index.add_folder(db, str(target), None)
        db.commit()
        db.close()
        build(home)
        assert [k for k in keys() if k.endswith("a.txt")] == [exclude.key(target / "a.txt")]
        assert rows("select id from files where key = ?", (exclude.key(target / "a.txt"),)) == [(real_id,)]
        assert rows("select id from recognized") == []  # 뺀 옛 줄의 기록도 지운다
    finally:
        os.rmdir(link)


def test_interrupted_first_build_then_one_step_shows_no_duplicates(home, tmp_path, monkeypatch):
    target, link = tmp_path / "실제", tmp_path / "연결"
    for i in range(5):
        write(target / f"f{i}.txt", "x" * (100 + i))  # 크기가 모두 달라 중복은 하나도 없다
    junction(link, target)
    try:
        settings.save({"roots": [str(link)], **STEPS_OFF})  # 업데이트 뒤 다시 저장한 설정(실제 경로)이어도 옛 줄을 옮긴다
        old_build(monkeypatch, [link])
        with monkeypatch.context() as m:  # 업데이트 뒤 첫 색인이 파일 셋을 본 뒤 멈춘다
            plain, seen = index.stamp, []
            m.setattr(index, "stamp", lambda st: (seen.append(1), len(seen) >= 3 and index.stop.set(), plain(st))[2])
            build(home)
        assert index.stop.is_set()
        index.stop.clear()
        index.build(index.roots(settings.load()), only="hash")
        found = [k for k in keys() if k.endswith(".txt")]
        assert sorted(found) == sorted(exclude.key(target / f"f{i}.txt") for i in range(5))
        assert search.listing({"view": "duplicates"})["rows"] == []
    finally:
        os.rmdir(link)


def test_rows_of_removed_root_go_also_in_one_step_build(home, tmp_path):
    other = tmp_path / "다른"
    write(other / "o.txt", "같은 내용")
    write(home / "h.txt", "같은 내용")
    settings.save({"roots": [str(home), str(other)], **STEPS_OFF})
    build(home)
    settings.save({"roots": [str(home)]})
    index.stop.clear()
    index.build(index.roots(settings.load()), only="hash")
    assert not any(k.startswith(exclude.key(other)) for k in keys())
    assert not any(k.startswith(exclude.key(other)) for (k,) in rows("select key from folders"))
    assert search.listing({"view": "duplicates"})["rows"] == []


def test_moved_finds_each_real_folder_once(home, monkeypatch):
    for i in range(20):
        write(home / f"d{i % 2}" / f"f{i}.txt")
    settings.save(STEPS_OFF)
    build(home)
    (home / "새").mkdir()
    pairs = []
    for i in range(20):
        before, after = home / f"d{i % 2}" / f"f{i}.txt", home / "새" / f"f{i}.txt"
        os.replace(before, after)
        pairs.append((str(before), str(after)))
    calls = []
    plain = index.real
    with monkeypatch.context() as m:
        m.setattr(index, "real", lambda p: (calls.append(p), plain(p))[1])
        index.moved(pairs)
    assert len(calls) == 3  # d0, d1, 새
    assert {k for k in keys() if k.startswith(exclude.key(home / "새"))} == {exclude.key(home / "새" / f"f{i}.txt") for i in range(20)}


def image_rows(paths):
    db = index.connect()
    ids = [db.execute("select id from files where key = ?", (exclude.key(p),)).fetchone()[0] for p in paths]
    for i, h in zip(ids, [0x0f0f0f0f0f0f0f0f, 0x0f0f0f0f0f0f0f0e]):
        db.execute("insert into media(id, kind, frames, made) values(?, 'image', ?, 0)", (i, f"{h:016x}"))
        db.execute("insert into media_todo(id) values(?)", (i,))
    db.commit()
    db.close()
    return ids


def test_photo_removed_while_matching_leaves_no_pair(home, monkeypatch):
    paths = [write(home / f"p{i}.jpg", "그림 아님" * (i + 1)) for i in range(2)]
    settings.save(STEPS_OFF)
    build(home)
    image_rows(paths)
    plain = media.distance
    done = []

    def remove_then(a, b):
        if not done:
            done.append(1)
            index.removed([paths[1]])  # 다른 연결(휴지통 등)이 그사이 뺐다
        return plain(a, b)

    with monkeypatch.context() as m:
        m.setattr(media, "distance", remove_then)
        db = index.connect()
        media.match_images(db)
        db.close()
    assert done and rows("select a, b from media_pair") == []


def test_removed_folder_row_disappears(home, tmp_path):
    write(home / "하위" / "a.txt")
    settings.save(STEPS_OFF)
    build(home)
    assert exclude.key(home / "하위") in {k for (k,) in rows("select key from folders")}
    os.rename(home / "하위", tmp_path / "밖")
    build(home)
    assert exclude.key(home / "하위") not in {k for (k,) in rows("select key from folders")}


def test_video_pairs_found_before_stop_are_written(home, monkeypatch):
    settings.save(STEPS_OFF)
    db = index.connect()
    for i in (1, 2, 3):
        db.execute("insert into media(id, kind, duration, frames, made, interval) values(?, 'video', 100, ?, 0, 120)", (i, media.encode([1, 2, 3, 4, 5])))
        db.execute("insert into media_todo(id) values(?)", (i,))
    db.execute("insert into meta(name, value) values('video_similar', 70)")
    db.commit()
    plain = media.similarity
    calls = []

    def similar(a, b):
        calls.append(1)
        if len(calls) == 2:  # 첫 동영상을 다 견준 뒤 멈춘다
            index.stop.set()
        return plain(a, b)

    try:
        with monkeypatch.context() as m:
            m.setattr(media, "similarity", similar)
            m.setattr(index, "SAVE_EVERY", 10 ** 9)  # 멈출 때 쓰지 않으면 남지 않게
            index.saved[0] = time.time()
            media.match_videos(db, 70)
    finally:
        index.stop.clear()
        db.close()
    assert sorted(rows("select a, b from media_pair")) == [(1, 2), (1, 3)]
    assert sorted(rows("select id from media_todo")) == [(2,), (3,)]
