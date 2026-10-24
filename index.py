"""색인(설정 폴더의 index.db, SQLite). 목록·검색·모아 보기가 디스크를 다시 훑지 않고 이것을 읽는다.
- files: 파일 하나에 한 줄(경로, 종류, 크기, 만든·고친·연 시각, 다운로드 표시, 중복 비교용 지문, 내용을 읽은 때의 stamp).
- body: 파일 안의 글자(FTS5 trigram, rowid = files.id). 세 글자 이상은 색인으로 빨리 찾는다.
- folders: 폴더 구조와 그 아래 전체의 파일 수·용량(사이드바의 위치, 상세의 경로).
색인은 ① 파일 목록(이름·크기·날짜)을 빠르게 훑은 뒤, 설정(settings.steps)의 순서로 켜 둔 단계를 한다:
중복 확인(바이트 지문), 파일 내용 읽기(글자, 문서의 글자 지문), 문서 비교(문서 정보를 뺀 Office 문서 지문), 사진·동영상 비교(media), 인식(recognize).
단계마다 아직 하지 않았거나 그 뒤에 바뀐 파일만 하므로, 멈추거나 창을 닫았다 다시 하면 남은 것부터 이어서 한다. 한 단계만 따로 돌릴 수도 있다(only).
SAVE_EVERY초마다 저장해서 중간에 끊겨도 거기까지는 남는다. 내용을 읽은 파일은 연 시각을 읽기 전으로 되돌린다.
바이트 지문은 크기가 같은 파일이 둘 이상일 때만 만든다(크기가 다르면 같은 파일일 수 없다)."""
import hashlib
import os
import sqlite3
import stat as stat_flags
import sys
import threading
import time
import zipfile
import zlib
from pathlib import Path

import blacklist
import drives
import exclude
import extract
import fence
import filetime
import games
import kinds
import settings

SKIP = {"windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin", "system volume information",
        "recovery", "config.msi", "$windows.~bt", "$windows.~ws", "$winreagent"}  # 들어가지 않는 폴더 이름(소문자)
HIDDEN = stat_flags.FILE_ATTRIBUTE_HIDDEN | stat_flags.FILE_ATTRIBUTE_SYSTEM
SAVE_EVERY = 2  # 이 초마다 저장해서, 색인하는 중에도 목록에 나오고 끊겨도 거기까지는 남게 한다.
WHOLE = 8 * 1024 * 1024  # 이보다 큰 파일은 앞·가운데·뒤 SAMPLE씩만 읽어 지문을 만든다(그래서 '중복 의심').
SAMPLE = 1024 * 1024
# 문서 비교: Office 문서(zip)에서 문서 정보(docProps: 만든·저장한 사람, 시각, 편집 시간)를 뺀 나머지로 만드는 지문.
DOC_EXTS = (".docx", ".xlsx", ".xlsm", ".pptx")
# 글자 지문을 만드는 형식(다시 저장해도 글자가 같으면 같다). 이보다 짧은 글자는 우연히 같기 쉬워 만들지 않는다('').
TEXT_EXTS = DOC_EXTS + (".pdf",)
TEXT_MIN = 20

SCHEMA = """
create table if not exists files(id integer primary key, path text, key text unique, folder text, name text, ext text, kind text,
  size integer, created integer, modified integer, accessed integer, downloaded integer, stamp integer, hash text, hashed integer,
  textstamp integer, dochash text, dochashed integer, texthash text);
create index if not exists files_folder on files(folder);
create virtual table if not exists body using fts5(text, tokenize='trigram');
create table if not exists folders(key text primary key, path text, parent text, name text, files integer default 0, size integer default 0);
create index if not exists folders_parent on folders(parent);
create index if not exists files_hash on files(hash);
create index if not exists files_dochash on files(dochash);
create index if not exists files_texthash on files(texthash);
-- 정렬·모아 보기용 B-트리 색인: 정렬하면 앞의 몇 줄만 읽는다(search.listing은 2차 정렬로 id를 써서 이 색인을 그대로 탄다).
create index if not exists files_name on files(lower(name));
create index if not exists files_size on files(size);
create index if not exists files_modified on files(modified);
create index if not exists files_seen on files(max(accessed, modified));
create index if not exists files_downloaded on files(downloaded, created);
create index if not exists files_kind on files(kind, ext);
-- 폴더별로 묶어 볼 때(search.first_keys): 폴더 안의 기본 정렬 방향(이름 오름차순, 크기·날짜 내림차순)에 맞춘다.
create index if not exists files_folder_name on files(folder, lower(name));
create index if not exists files_folder_size on files(folder, size desc);
create index if not exists files_folder_date on files(folder, modified desc);
create table if not exists media(id integer primary key, kind text, duration real, frames text, made integer, interval integer);
drop table if exists media_band;
create table if not exists meta(name text primary key, value);
create table if not exists media_todo(id integer primary key);
create table if not exists media_pair(a integer, b integer, score integer, primary key(a, b));
create index if not exists media_pair_b on media_pair(b);
create table if not exists tags(id integer, kind text, label text, score integer);
create index if not exists tags_id on tags(id);
create index if not exists tags_kind on tags(kind, label);
create table if not exists recognized(id integer primary key, made integer, custom text, faces integer);
create table if not exists faces(id integer, n integer, person integer, vec blob);
create table if not exists games(key text primary key, path text, size integer);
create index if not exists faces_id on faces(id);
create index if not exists faces_person on faces(person);
"""
# 예전 색인 파일에 없는 칸. dochash: 문서 비교 지문(dochashed: 만든 때의 stamp), texthash: 글자 지문(''은 만들지 않음).
ADDED = {"hash": "text", "hashed": "integer", "textstamp": "integer", "dochash": "text", "dochashed": "integer", "texthash": "text"}

# 화면이 1초마다 읽는다. step: 지금 단계 이름, done/total: 그 단계에서 한 수와 할 수(목록 단계는 total 없음).
status = {"running": False, "step": "", "done": 0, "total": 0, "current": "", "error": "", "finished": None, "stopped": False}
lock = threading.Lock()
stop = threading.Event()  # 켜지면 색인이 지금 파일까지만 하고 멈춘다.
worker = None  # 색인 스레드(창을 닫을 때 끝나기를 기다린다)
saved = [0.0]  # 마지막으로 저장한 때
ready = set()  # 이 프로세스에서 표를 맞춘 색인 파일들. 열 때마다 맞추면 한 번에 6ms라, 파일 500개 검사에 7초가 걸렸다.


def connect():
    path = settings.folder() / "index.db"
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    if path in ready:
        return db
    db.execute("pragma journal_mode=wal")  # 색인하는 동안에도 읽을 수 있게(파일에 남는 설정이라 한 번이면 된다)
    have = {row["name"] for row in db.execute("pragma table_info(files)")}
    for name, kind in ADDED.items():
        if have and name not in have:
            db.execute(f"alter table files add column {name} {kind}")
    db.executescript(SCHEMA)
    ready.add(path)
    return db


def hidden(entry_stat):
    return entry_stat.st_file_attributes & HIDDEN


def roots(config):
    """색인할 폴더들: 설정의 폴더와 놓을 곳. 없는 폴더, 다른 폴더 안에 든 폴더, 차단·잠금 폴더(또는 그 아래)는 뺀다."""
    found, fences = [], fence.keys()
    for path in config["roots"] + [config["dest"]]:
        if path and os.path.isdir(path) and not fence.inside(path, fences):
            found.append(os.path.abspath(path))
    keys = {exclude.key(path): path for path in found}
    return [path for k, path in keys.items() if not fence.inside(k, keys.keys() - {k})]


def storable(text):
    """색인(SQLite)에 넣을 수 있는 이름·경로인지: 짝 없는 서로게이트 글자가 든 이름은 넣지 못해 건너뛴다."""
    try:
        text.encode("utf-8")
        return True
    except UnicodeError:
        return False


def walk(root, fences=frozenset(), game_keys=frozenset(), found_games=None, never_game=frozenset()):
    """root 아래의 (폴더 경로, [파일 DirEntry]) 들. 링크·정션, 숨김·시스템 폴더, SKIP의 폴더, 차단·잠금 폴더(fences, key)는
    들어가지 않는다. 읽을 수 없는 폴더는 건너뛴다.
    게임 폴더(런처가 아는 폴더 game_keys, 게임 폴더 이름 blacklist.GAMES, 안의 이름으로 판별 games.is_game)는 판별만 하고
    안을 읽지 않는다. 찾은 게임 폴더는 found_games에 더한다. never_game(key: 색인할 맨 위 폴더, 다운로드 등 알려진 폴더,
    사용자 폴더)은 안의 이름으로 게임이라 판별하지 않는다(다운로드에 steam_api64.dll 하나가 있다고 다운로드 전체가 게임이 되지 않게)."""
    stack = [root]
    while stack:
        folder = stack.pop()
        files, folders = [], []
        if exclude.key(folder) in game_keys or os.path.basename(folder).lower() in blacklist.GAMES:
            if found_games is not None:
                found_games += [folder] if exclude.key(folder) in game_keys else [g for g in games.container_games(folder) if storable(g)]
            continue
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if not storable(entry.name):
                            continue
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir():
                            if entry.name.lower() not in SKIP and not hidden(entry.stat()) and exclude.key(entry.path) not in fences:
                                folders.append(entry.path)
                        elif entry.is_file() and not hidden(entry.stat()):
                            files.append(entry)
                    except OSError:
                        continue
        except OSError:
            continue
        if exclude.key(folder) not in never_game and games.is_game({e.name.lower() for e in files}, {os.path.basename(p).lower() for p in folders}):
            if found_games is not None:
                found_games.append(folder)
            continue
        stack += folders
        yield folder, files


def save_now(db, force=False):
    """SAVE_EVERY초가 지났으면(또는 force) 저장한다."""
    if force or time.time() - saved[0] >= SAVE_EVERY:
        db.commit()
        saved[0] = time.time()


def save_file(db, path, st):
    """파일 하나의 목록 정보를 색인에 넣거나 고친다(다운로드 표시 포함). 바뀐 파일은 지문과 내용을 지워 ②·③에서 다시 하게 한다."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    marked = filetime.downloaded(path)
    filetime.restore_access(path, st)
    key = exclude.key(path)
    row = db.execute(
        """insert into files(path, key, folder, name, ext, kind, size, created, modified, accessed, downloaded, stamp)
           values(?,?,?,?,?,?,?,?,?,?,?,?)
           on conflict(key) do update set path=excluded.path, folder=excluded.folder, name=excluded.name, ext=excluded.ext,
             kind=excluded.kind, size=excluded.size, created=excluded.created, modified=excluded.modified,
             accessed=excluded.accessed, downloaded=excluded.downloaded, stamp=excluded.stamp, hash=null, hashed=null, textstamp=null,
             dochash=null, dochashed=null, texthash=null
           returning id""",
        (path, key, os.path.dirname(key), name, ext, kinds.of(ext), st.st_size, int(st.st_birthtime), int(st.st_mtime),
         int(st.st_atime), int(marked), st.st_mtime_ns)).fetchone()
    drop_ids(db, [row["id"]])  # 바뀐 파일: 사진·동영상 지문과 짝도 다시 만든다.


def add_folder(db, path, parent):
    key = exclude.key(path)
    db.execute("""insert into folders(key, path, parent, name) values(?,?,?,?)
                  on conflict(key) do update set path=excluded.path, parent=excluded.parent, name=excluded.name""",
               (key, path, parent and exclude.key(parent), os.path.basename(path.rstrip("\\")) or path))


def totals(db):
    """폴더마다 그 아래 전체(하위 폴더 포함)의 파일 수·용량을 다시 센다. 안을 읽지 않은 게임 폴더는 런처가 아는 크기나
    센 크기(games 표)를 더한다(왼쪽 폴더 목록에서 큰 게임 폴더가 빠지지 않게)."""
    saved = {key: (parent, files, size) for key, parent, files, size in db.execute("select key, parent, files, size from folders")}
    parent = {key: row[0] for key, row in saved.items()}
    count = dict.fromkeys(parent, 0)
    size = dict.fromkeys(parent, 0)
    game_sizes = {exclude.key(g["path"]): g["size"] for g in games.launcher_games() if g.get("size")}
    game_sizes.update({key: s for key, s in db.execute("select key, size from games where size is not null")})
    rows = [(row["folder"], row["n"], row["s"] or 0) for row in db.execute("select folder, count(*) n, sum(size) s from files group by folder")]
    for folder, n, s in rows + [(key, 0, s) for key, s in game_sizes.items()]:
        while folder in parent:  # 그 폴더와 위의 모든 폴더에 더한다.
            count[folder] += n
            size[folder] += s
            folder = parent[folder]
    db.executemany("update folders set files = ?, size = ? where key = ?", [(count[k], size[k], k) for k in parent if saved[k][1:] != (count[k], size[k])])


def step_work(name):
    """단계 이름 → 그 단계를 하는 함수(db). 사진·동영상 단계는 그때 불러온다(무거운 라이브러리를 켤 때 올리지 않게)."""
    if name == "media":
        import media
        return media.run
    if name == "recognize":
        import recognize
        return recognize.run
    return {"hash": fingerprints, "text": read_texts, "doc": documents}[name]


def build(paths, only=None):
    """paths 아래를 훑어 색인을 맞추고(①), 켜 둔 단계를 설정의 순서로 한다. 끝까지 훑었으면 사라진 파일·폴더를 지운다.
    only: 그 단계 하나만 한다(파일 목록은 훑지 않는다). 멈추면(stop) 그때까지 한 것은 남기고 끝낸다.
    사라진 것 지우기는 끝까지 훑었을 때만 한다(못 본 파일이 사라진 것인지 알 수 없으므로)."""
    db = connect()
    try:
        if only:
            step_work(only)(db)
            return
        old = {row["key"]: (row["size"], row["stamp"], row["accessed"], sys.intern(row["kind"] or ""))
               for row in db.execute("select key, size, stamp, accessed, kind from files")}  # 종류 글자는 하나로(파일 수십만 개의 기억을 줄인다)
        seen, folders, found_games = set(), set(), []
        status.update(step="파일 목록 확인", done=0, total=0)
        done = scan(db, paths, old, seen, folders, found_games)
        if done:
            sizes = dict(db.execute("select key, size from games").fetchall())  # 세어 둔 크기는 남긴다.
            launcher = {exclude.key(g["path"]) for g in games.launcher_games()}  # 런처가 아는 게임의 센 크기도 남긴다.
            db.executemany("delete from games where key = ?", [(k,) for k in sizes if k not in launcher])
            db.executemany("insert or ignore into games(key, path, size) values(?, ?, ?)",
                           [(exclude.key(p), p, sizes.get(exclude.key(p))) for p in found_games])
            games.forget()
            config = settings.load()
            missing = {exclude.key(p) for p in config["roots"] + [config["dest"]] if p and not os.path.isdir(p) and not fence.inside(p, {exclude.key(r) for r in paths})}
            gone = [(key,) for key in old if key not in seen and not fence.inside(key, missing)]
            drop_ids(db, [row[0] for key in gone for row in db.execute("select id from files where key = ?", key)])
            db.executemany("delete from files where key = ?", gone)
            db.executemany("delete from folders where key = ?",
                           [(key,) for (key,) in db.execute("select key from folders") if key not in folders and not fence.inside(key, missing)])
        totals(db)
        save_now(db, force=True)
        for name in settings.steps(settings.load()):
            if stop.is_set():
                break
            step_work(name)(db)
    finally:
        db.close()


def scan(db, paths, old, seen, folders, found_games):
    """① paths 아래의 파일 목록을 색인에 맞춘다. 본 파일·폴더의 key를 seen·folders에, 게임 폴더를 found_games에 모은다.
    끝까지 했으면 True, 멈췄으면 False."""
    fences = fence.keys()
    launcher = [g["path"] for g in games.launcher_games()]
    game_keys = {exclude.key(p) for p in launcher}
    never_game = {exclude.key(p) for p in [*paths, *drives.known_folders().values(), str(Path.home())]}
    for root in paths:
        for folder, entries in walk(root, fences, game_keys, found_games, never_game):
            if stop.is_set():
                return False
            add_folder(db, folder, None if folder == root else os.path.dirname(folder))
            folders.add(exclude.key(folder))
            for entry in entries:
                if stop.is_set():
                    return False
                try:  # 폴더 목록(DirEntry)의 시각은 늦게 고쳐질 때가 있어 파일에 직접 묻는다.
                    st = os.stat(entry.path)
                except OSError:
                    continue
                key = exclude.key(entry.path)
                seen.add(key)
                known = old.get(key)
                if known and known[:2] == (st.st_size, st.st_mtime_ns):  # 바뀌지 않았다: 연 시각과 종류(종류 표가 바뀌었을 수 있다)만, 달라졌을 때 고친다.
                    now = (int(st.st_atime), kinds.of(os.path.splitext(entry.name)[1].lower()))
                    if known[2:4] != now:
                        db.execute("update files set accessed = ?, kind = ? where key = ?", (*now, key))
                else:
                    status["current"] = entry.path
                    save_file(db, entry.path, st)
                status["done"] += 1
                save_now(db)
    for path in found_games + launcher:  # 게임 폴더와 그 위 묶음 폴더(SteamLibrary\steamapps\common 등)도 폴더 목록에 둔다.
        add_game_chain(db, path, folders, fences)  # 용량은 totals가 더한다.
    return True


def add_game_chain(db, path, folders, fences):
    """path에서 색인한 위 폴더까지 이어지는 폴더들을 폴더 목록에 넣는다. 색인한 곳 밖이거나, 중간에 들어가지 않는 폴더
    (SKIP: Windows·Program Files 등, 차단·잠금 폴더)가 끼면 넣지 않는다(그 폴더가 목록에 나타나지 않게)."""
    chain, folder = [], os.path.abspath(path)
    while exclude.key(folder) not in folders:
        if os.path.basename(folder).lower() in SKIP or exclude.key(folder) in fences or os.path.dirname(folder) == folder:
            return
        chain.append(folder)
        folder = os.path.dirname(folder)
    for item in reversed(chain):
        add_folder(db, item, os.path.dirname(item))
        folders.add(exclude.key(item))


def read_text(db, row_id, path, ext, st):
    """파일 하나의 글자를 읽어 넣고 읽은 것으로 표시한다. 읽은 뒤 연 시각은 되돌린다."""
    text = extract.text(path, ext) if extract.readable(ext, st.st_size) else ""
    filetime.restore_access(path, st)
    if text is None:  # 읽다 실패했다(클라우드 전용·잠시 잠긴 파일 등): 읽은 것으로 표시하지 않아 다음 색인 때 다시 읽는다.
        return
    db.execute("delete from body where rowid = ?", (row_id,))
    if text.strip():
        db.execute("insert into body(rowid, text) values(?, ?)", (row_id, text))
    db.execute("update files set textstamp = stamp, texthash = ? where id = ?", (text_hash(text) if ext in TEXT_EXTS else None, row_id))


def text_hash(text):
    """글자 지문: 띄어쓰기·줄바꿈을 하나로 맞춘 글자의 SHA-256. 너무 짧으면 ''(우연히 같은 짧은 문서를 묶지 않게)."""
    plain = " ".join(text.split())
    return hashlib.sha256(plain.encode("utf-8")).hexdigest() if len(plain) >= TEXT_MIN else ""


def read_texts(db):
    """③ 내용을 읽을 수 있는 파일 중 아직 읽지 않았거나 그 뒤에 바뀐 파일의 글자를 읽는다. 읽은 뒤 연 시각은 되돌린다."""
    readable = sorted(extract.READERS)
    marks = ", ".join("?" * len(readable))
    rows = db.execute(f"""select id, path, ext, size, stamp from files where (textstamp is null or textstamp <> stamp)
                          and size <= ? and ext in ({marks})""", [extract.MAX_SIZE] + readable).fetchall()
    fill_text_hashes(db)
    status.update(step="파일 내용 읽기", done=0, total=len(rows))
    for row in rows:
        if stop.is_set():
            break
        status["current"] = row["path"]
        try:
            st = os.stat(row["path"])
        except OSError:
            continue
        if st.st_mtime_ns != row["stamp"]:  # 목록을 훑은 뒤에 바뀌었다: 다음 색인 때 읽는다.
            continue
        read_text(db, row["id"], row["path"], row["ext"], st)
        status["done"] += 1
        save_now(db)
    save_now(db, force=True)


def fill_text_hashes(db):
    """글자 지문이 생기기 전에 읽어 둔 문서: 저장된 글자로 지문만 만든다(파일을 다시 읽지 않는다)."""
    marks = ", ".join("?" * len(TEXT_EXTS))
    rows = db.execute(f"""select f.id, b.text from files f left join body b on b.rowid = f.id
                          where f.textstamp = f.stamp and f.texthash is null and f.ext in ({marks})""", TEXT_EXTS).fetchall()
    db.executemany("update files set texthash = ? where id = ?", [(text_hash(row["text"] or ""), row["id"]) for row in rows])


def doc_fingerprint(path):
    """문서 비교 지문: zip 안의 파일 중 문서 정보(docProps/…)를 뺀 나머지의 이름과 내용으로 만든 SHA-256.
    복사한 뒤 문서 정보만 바뀐 문서끼리 같다. 열지 못하는 파일(깨짐·암호)은 ''."""
    digest = hashlib.sha256()
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().startswith("docprops/") or name.endswith("/"):
                    continue
                part = hashlib.sha256()
                with archive.open(name) as member:  # 조금씩 읽는다(동영상이 든 문서·압축 폭탄도 메모리에 다 올리지 않게)
                    for chunk in iter(lambda: member.read(1024 * 1024), b""):
                        part.update(chunk)
                digest.update(name.encode("utf-8") + b"\0" + part.digest())
    except (zipfile.BadZipFile, RuntimeError, ValueError, EOFError, zlib.error):  # zlib.error: 중간이 깨진 문서
        return ""
    return digest.hexdigest()


def documents(db):
    """문서 비교: Office 문서 중 지문이 없거나 그 뒤에 바뀐 것의 문서 비교 지문을 만든다. 읽은 뒤 연 시각은 되돌린다."""
    marks = ", ".join("?" * len(DOC_EXTS))
    rows = db.execute(f"select id, path, stamp from files where (dochashed is null or dochashed <> stamp) and ext in ({marks})", DOC_EXTS).fetchall()
    status.update(step="문서 비교", done=0, total=len(rows))
    for row in rows:
        if stop.is_set():
            break
        status["current"] = row["path"]
        try:
            st = os.stat(row["path"])
            if st.st_mtime_ns != row["stamp"]:  # 목록을 훑은 뒤에 바뀌었다: 다음 색인 때 한다.
                continue
            value = doc_fingerprint(row["path"])
        except OSError:
            continue
        filetime.restore_access(row["path"], st)
        db.execute("update files set dochash = ?, dochashed = stamp where id = ?", (value, row["id"]))
        status["done"] += 1
        save_now(db)
    save_now(db, force=True)


def fingerprint(path, size):
    """파일 내용의 지문. 크기를 앞에 붙여 크기가 다른 파일과는 겹치지 않게 한다. 읽은 뒤 연 시각은 되돌린다."""
    st = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        if size <= WHOLE:
            digest.update(file.read())
        else:
            for start in (0, size // 2, size - SAMPLE):
                file.seek(start)
                digest.update(file.read(SAMPLE))
    filetime.restore_access(path, st)
    return f"{size}:{digest.hexdigest()}"


def fingerprints(db):
    """크기가 같은 파일이 둘 이상인 것들 중 지문이 없거나 그 뒤에 바뀐 파일의 지문을 만든다."""
    rows = db.execute("""select id, path, size from files where size > 0 and (hashed is null or hashed <> stamp)
                         and size in (select size from files where size > 0 group by size having count(*) > 1)""").fetchall()
    status.update(step="중복 확인", done=0, total=len(rows))
    for row in rows:
        if stop.is_set():
            break
        status["current"] = row["path"]
        try:
            value = fingerprint(row["path"], row["size"])
        except OSError:
            continue
        db.execute("update files set hash = ?, hashed = stamp where id = ?", (value, row["id"]))
        status["done"] += 1
        save_now(db)
    save_now(db, force=True)


def start(paths, only=None):
    """색인을 따로 돌린다(only: 그 단계 하나만). 이미 돌고 있으면 False."""
    with lock:
        if status["running"]:
            return False
        status.update(running=True, step="", done=0, total=0, current="", error="", stopped=False)
        stop.clear()

    def work():
        try:
            build(paths, only)
        except Exception as e:  # 화면에 알리고 끝낸다.
            status["error"] = str(e)
        finally:
            status.update(running=False, current="", finished=int(time.time()), stopped=stop.is_set())

    global worker
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    return True


def finish(timeout=15):
    """창을 닫을 때: 돌고 있는 색인을 멈추고, 지금 파일까지 저장하고 끝나기를 기다린다."""
    stop.set()
    if worker is not None:
        worker.join(timeout)


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


def add_chain(db, folder, known):
    """색인한 폴더 known부터 folder까지 색인에 없는 폴더를 만든다."""
    chain = []
    path = folder
    while exclude.key(path) != exclude.key(known):
        chain.append(path)
        path = os.path.dirname(path)
    for path in reversed(chain):
        add_folder(db, path, os.path.dirname(path))


def prefix(path):
    return os.path.join(exclude.key(path), "")


def move_folder_rows(db, before, after):
    """폴더 before(와 그 아래 전부)를 after로 옮긴 것을 반영한다. 내용·지문은 그대로 두고 경로만 바꾼다."""
    where, values = below(before)
    folders = db.execute(f"select key, path from folders where {where}", values).fetchall()
    paths = [(after + row["path"][len(before):], row["key"]) for row in folders]
    db.executemany("update folders set key = ?, path = ?, parent = ?, name = ? where key = ?",
                   [(exclude.key(path), path, exclude.key(os.path.dirname(path)), os.path.basename(path), key) for path, key in paths])
    files = [(after + row["path"][len(before):], row["id"]) for row in db.execute(f"select id, path from files where {where}", values)]
    db.executemany("update files set path = ?, key = ?, folder = ? where id = ?",
                   [(path, exclude.key(path), os.path.dirname(exclude.key(path)), id_) for path, id_ in files])


def moved(pairs):
    """정리·되돌리기·옮기기·이름 바꾸기로 옮긴 (전, 후) 경로(파일 또는 폴더)를 색인에 반영한다. 옮겨 간 곳이 색인 밖이면 색인에서 뺀다."""
    db = connect()
    try:
        for before, after in pairs:
            if not storable(after):  # 색인에 넣지 못하는 이름: 옛 기록만 뺀다
                forget(db, before)
                continue
            folder = os.path.dirname(after)
            known = under_index(db, folder)
            if known is None:
                forget(db, before)
                continue
            add_chain(db, folder, known)
            if exclude.key(after) != exclude.key(before):  # 대소문자만 바꾼 이름 바꾸기는 자기 줄이다.
                forget(db, after)
            if os.path.isdir(after):
                if db.execute("select 1 from folders where key = ?", (exclude.key(before),)).fetchone():
                    move_folder_rows(db, before, after)
                else:  # 색인 밖에서 들어온 폴더(색인 밖으로 옮겼다 되돌린 것 등): 그 아래를 바로 넣는다.
                    add_tree(db, after)
                continue
            row = db.execute("select id from files where key = ?", (exclude.key(before),)).fetchone()
            if row:  # 내용은 그대로 두고 위치·이름만 바꾼다.
                key = exclude.key(after)
                ext = os.path.splitext(after)[1].lower()
                db.execute("update files set path = ?, key = ?, folder = ?, name = ?, ext = ?, kind = ? where id = ?",
                           (after, key, os.path.dirname(key), os.path.basename(after), ext, kinds.of(ext), row["id"]))
            else:  # 색인에 없던 파일이 들어왔다(정리 등): 몇 개뿐이니 내용까지 바로 읽는다.
                st = os.stat(after)
                save_file(db, after, st)
                row = db.execute("select id from files where key = ?", (exclude.key(after),)).fetchone()
                read_text(db, row["id"], after, os.path.splitext(after)[1].lower(), st)
        totals(db)
        db.commit()
    finally:
        db.close()


def add_tree(db, path):
    """폴더 path와 그 아래(색인과 같은 규칙으로 걸러서)를 색인에 넣는다. 글자 읽기 등은 다음 색인 단계가 한다."""
    never_game = {exclude.key(p) for p in [*roots(settings.load()), *drives.known_folders().values(), str(Path.home())]}
    for folder, entries in walk(path, fence.keys(), games.keys(), [], never_game):
        add_folder(db, folder, os.path.dirname(folder))
        for entry in entries:
            try:
                save_file(db, entry.path, os.stat(entry.path))
            except OSError:
                continue


def drop_ids(db, ids):
    """files에서 지울 줄들(id)의 글자·사진·동영상 지문·인식 기록을 지운다(files 줄은 부르는 쪽이 지운다)."""
    for table, column in (("body", "rowid"), ("media", "id"), ("media_todo", "id"), ("media_pair", "a"), ("media_pair", "b"),
                          ("tags", "id"), ("recognized", "id"), ("faces", "id")):
        db.executemany(f"delete from {table} where {column} = ?", [(i,) for i in ids])


def below(path):
    """path(파일 또는 폴더)와 그 아래 전부의 key 조건과 값. '폴더\' 이상 '폴더]' 미만인 범위라 key 색인을 탄다(search.under와 같다)."""
    old = prefix(path)
    return "(key = ? or (key >= ? and key < ?))", (exclude.key(path), old, old[:-1] + "]")


def forget(db, path):
    """파일 path, 또는 폴더 path와 그 아래 전부를 색인에서 뺀다."""
    where, values = below(path)
    drop_ids(db, [row[0] for row in db.execute(f"select id from files where {where}", values)])
    db.execute(f"delete from files where {where}", values)
    db.execute(f"delete from folders where {where}", values)


def removed(paths):
    """휴지통으로 보낸 파일·폴더들을 색인에서 뺀다."""
    db = connect()
    try:
        for path in paths:
            forget(db, path)
        totals(db)
        db.commit()
    finally:
        db.close()


def look(paths):
    """paths 각각의 (색인에 있는지, 색인한 맨 위 폴더인지). 연결 하나로 본다(파일마다 열고 닫으면 500개에 8초가 걸렸다)."""
    db = connect()
    try:
        found = {}
        for path in paths:
            key = exclude.key(path)
            known_here = db.execute("select 1 from files where key = ? union select 1 from folders where key = ?", (key, key)).fetchone()
            root = db.execute("select 1 from folders where key = ? and parent is null", (key,)).fetchone()
            found[path] = (bool(known_here), bool(root))
        return found
    finally:
        db.close()


def is_root(path):
    """색인한 맨 위 폴더인지(옮기거나 휴지통으로 보내지 않는다)."""
    return look([path])[path][1]


def count():
    db = connect()
    try:
        return db.execute("select count(*) from files").fetchone()[0]
    finally:
        db.close()

