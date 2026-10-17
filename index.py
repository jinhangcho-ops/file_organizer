"""색인(설정 폴더의 index.db, SQLite). 검색·나무·모아 보기가 디스크를 다시 훑지 않고 이것을 읽는다.
- files: 파일 하나에 한 줄(경로, 종류, 크기, 만든·고친·연 시각, 다운로드 표시).
- body: 파일 안의 글자(FTS5 trigram, rowid = files.id). 세 글자 이상은 색인으로 빨리 찾는다.
- folders: 폴더 구조와 그 아래 전체의 파일 수·용량(나무 보기용).
두 번째 색인부터는 크기·고친 시각이 바뀐 파일만 내용을 다시 읽는다. 내용을 읽은 파일은 연 시각을 읽기 전으로 되돌린다."""
import os
import sqlite3
import stat as stat_flags
import threading
import time

import exclude
import extract
import filetime
import kinds
import settings

SKIP = {"windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin", "system volume information",
        "recovery", "config.msi", "$windows.~bt", "$windows.~ws", "$winreagent"}  # 들어가지 않는 폴더 이름(소문자)
HIDDEN = stat_flags.FILE_ATTRIBUTE_HIDDEN | stat_flags.FILE_ATTRIBUTE_SYSTEM
COMMIT_EVERY = 500  # 이만큼 넣을 때마다 저장해서, 색인하는 중에도 검색에 나오게 한다.

SCHEMA = """
create table if not exists files(id integer primary key, path text, key text unique, folder text, name text, ext text, kind text,
  size integer, created integer, modified integer, accessed integer, downloaded integer, stamp integer);
create index if not exists files_folder on files(folder);
create virtual table if not exists body using fts5(text, tokenize='trigram');
create table if not exists folders(key text primary key, path text, parent text, name text, files integer default 0, size integer default 0);
create index if not exists folders_parent on folders(parent);
"""

status = {"running": False, "done": 0, "current": "", "error": "", "finished": None}  # 화면이 2초마다 읽는다.
lock = threading.Lock()


def connect():
    db = sqlite3.connect(settings.folder() / "index.db", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode=wal")  # 색인하는 동안에도 읽을 수 있게
    db.executescript(SCHEMA)
    return db


def hidden(entry_stat):
    return entry_stat.st_file_attributes & HIDDEN


def roots(config):
    """색인할 폴더들: 설정의 폴더와 놓을 곳. 없는 폴더와 다른 폴더 안에 든 폴더는 뺀다."""
    found = []
    for path in config["roots"] + [config["dest"]]:
        if path and os.path.isdir(path):
            found.append(os.path.abspath(path))
    keys = {exclude.key(path): path for path in found}
    return [path for k, path in keys.items() if not any(k != other and k.startswith(os.path.join(other, "")) for other in keys)]


def walk(root):
    """root 아래의 (폴더 경로, [파일 DirEntry]) 들. 링크·정션, 숨김·시스템 폴더, SKIP의 폴더는 들어가지 않는다. 읽을 수 없는 폴더는 건너뛴다."""
    stack = [root]
    while stack:
        folder = stack.pop()
        files = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir():
                            if entry.name.lower() not in SKIP and not hidden(entry.stat()):
                                stack.append(entry.path)
                        elif entry.is_file() and not hidden(entry.stat()):
                            files.append(entry)
                    except OSError:
                        continue
        except OSError:
            continue
        yield folder, files


def save_file(db, path, st, read_text=True):
    """파일 하나를 색인에 넣거나 고친다. 다운로드 표시와 내용을 읽고, 연 시각은 읽기 전으로 되돌린다."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    marked = filetime.downloaded(path)
    text = extract.text(path, ext) if read_text and extract.readable(ext, st.st_size) else ""
    filetime.restore_access(path, st)
    key = exclude.key(path)
    row = db.execute(
        """insert into files(path, key, folder, name, ext, kind, size, created, modified, accessed, downloaded, stamp)
           values(?,?,?,?,?,?,?,?,?,?,?,?)
           on conflict(key) do update set path=excluded.path, folder=excluded.folder, name=excluded.name, ext=excluded.ext,
             kind=excluded.kind, size=excluded.size, created=excluded.created, modified=excluded.modified,
             accessed=excluded.accessed, downloaded=excluded.downloaded, stamp=excluded.stamp
           returning id""",
        (path, key, os.path.dirname(key), name, ext, kinds.of(ext), st.st_size, int(st.st_birthtime), int(st.st_mtime),
         int(st.st_atime), int(marked), st.st_mtime_ns)).fetchone()
    db.execute("delete from body where rowid = ?", (row["id"],))
    if text.strip():
        db.execute("insert into body(rowid, text) values(?, ?)", (row["id"], text))


def add_folder(db, path, parent):
    key = exclude.key(path)
    db.execute("insert into folders(key, path, parent, name) values(?,?,?,?) on conflict(key) do nothing",
               (key, path, parent and exclude.key(parent), os.path.basename(path.rstrip("\\")) or path))


def totals(db):
    """폴더마다 그 아래 전체(하위 폴더 포함)의 파일 수·용량을 다시 센다."""
    parent = dict(db.execute("select key, parent from folders").fetchall())
    count = dict.fromkeys(parent, 0)
    size = dict.fromkeys(parent, 0)
    for row in db.execute("select folder, count(*) n, sum(size) s from files group by folder"):
        folder = row["folder"]
        while folder in parent:  # 그 폴더와 위의 모든 폴더에 더한다.
            count[folder] += row["n"]
            size[folder] += row["s"] or 0
            folder = parent[folder]
    db.executemany("update folders set files = ?, size = ? where key = ?", [(count[k], size[k], k) for k in parent])


def build(paths):
    """paths 아래를 모두 훑어 색인을 새로 맞춘다. 사라진 파일·폴더는 지운다."""
    db = connect()
    try:
        old = {row["key"]: (row["size"], row["stamp"]) for row in db.execute("select key, size, stamp from files")}
        seen = set()
        db.execute("delete from folders")
        for root in paths:
            for folder, entries in walk(root):
                add_folder(db, folder, None if folder == root else os.path.dirname(folder))
                for entry in entries:
                    try:  # 폴더 목록(DirEntry)의 시각은 늦게 고쳐질 때가 있어 파일에 직접 묻는다.
                        st = os.stat(entry.path)
                    except OSError:
                        continue
                    key = exclude.key(entry.path)
                    seen.add(key)
                    if old.get(key) == (st.st_size, st.st_mtime_ns):  # 바뀌지 않았다: 연 시각만 고친다.
                        db.execute("update files set accessed = ? where key = ?", (int(st.st_atime), key))
                    else:
                        status["current"] = entry.path
                        save_file(db, entry.path, st)
                    status["done"] += 1
                    if status["done"] % COMMIT_EVERY == 0:
                        db.commit()
        gone = [(key,) for key in old if key not in seen]
        db.executemany("delete from body where rowid = (select id from files where key = ?)", gone)
        db.executemany("delete from files where key = ?", gone)
        totals(db)
        db.commit()
    finally:
        db.close()


def start(paths):
    """색인을 따로 돌린다. 이미 돌고 있으면 False."""
    with lock:
        if status["running"]:
            return False
        status.update(running=True, done=0, current="", error="")

    def work():
        try:
            build(paths)
        except Exception as e:  # 화면에 알리고 끝낸다.
            status["error"] = str(e)
        finally:
            status.update(running=False, current="", finished=int(time.time()))

    threading.Thread(target=work, daemon=True).start()
    return True


def under_index(db, folder):
    """folder가 색인한 폴더(또는 그 아래)인지. 색인한 폴더 중 가장 가까운 위 폴더의 경로나 None."""
    path = folder
    while True:
        if db.execute("select 1 from folders where key = ?", (exclude.key(path),)).fetchone():
            return path
        up = os.path.dirname(path)
        if up == path:
            return None
        path = up


def moved(pairs):
    """정리·되돌리기로 옮긴 (전, 후) 경로를 색인에 반영한다. 옮겨 간 곳이 색인 밖이면 색인에서 뺀다."""
    db = connect()
    try:
        for before, after in pairs:
            folder = os.path.dirname(after)
            known = under_index(db, folder)
            row = db.execute("select id from files where key = ?", (exclude.key(before),)).fetchone()
            if known is None:
                if row:
                    db.execute("delete from body where rowid = ?", (row["id"],))
                    db.execute("delete from files where id = ?", (row["id"],))
                continue
            chain = []  # 색인한 폴더부터 새 폴더까지 없는 폴더를 만든다.
            path = folder
            while exclude.key(path) != exclude.key(known):
                chain.append(path)
                path = os.path.dirname(path)
            for path in reversed(chain):
                add_folder(db, path, os.path.dirname(path))
            if row:  # 내용은 그대로 두고 위치만 바꾼다.
                key = exclude.key(after)
                db.execute("update files set path = ?, key = ?, folder = ?, name = ? where id = ?",
                           (after, key, os.path.dirname(key), os.path.basename(after), row["id"]))
            else:
                save_file(db, after, os.stat(after))
        totals(db)
        db.commit()
    finally:
        db.close()


def count():
    db = connect()
    try:
        return db.execute("select count(*) from files").fetchone()[0]
    finally:
        db.close()


def known(path):
    """path가 색인에 있는 파일·폴더인지. 화면이 열어 달라는 경로는 이것으로 확인한다."""
    db = connect()
    try:
        key = exclude.key(path)
        return bool(db.execute("select 1 from files where key = ? union select 1 from folders where key = ?", (key, key)).fetchone())
    finally:
        db.close()
