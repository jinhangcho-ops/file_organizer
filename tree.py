"""폴더 나무: 왼쪽의 폴더 나무(색인한 맨 위 폴더와 그 아래 폴더, 폴더마다 그 아래 전체 파일 수)와
고른 파일·폴더까지의 경로(맨 위 폴더부터). 색인을 읽는다. 폴더를 펼칠 때 그 폴더의 바로 아래 폴더만 읽는다."""
import os

import exclude
import index
import search
import settings

FOLDER = "path, name, files, size, exists(select 1 from folders c where c.parent = folders.key) has_children"


def places():
    """색인한 맨 위 폴더들을 설정에 적은 순서로(놓을 곳은 끝에). [{"path", "name", "files", "size", "has_children"}]"""
    order = [exclude.key(path) for path in index.roots(settings.load())]
    db = index.connect()
    try:
        rows = [dict(row) for row in db.execute(f"select key, {FOLDER} from folders where parent is null")]
    finally:
        db.close()
    rows.sort(key=lambda row: order.index(row["key"]) if row["key"] in order else len(order))
    return [{k: v for k, v in row.items() if k != "key"} for row in rows]


def children(path):
    """폴더 path의 바로 아래 폴더들(이름 순)."""
    db = index.connect()
    try:
        return [dict(row) for row in db.execute(f"select {FOLDER} from folders where parent = ? order by lower(name)", (exclude.key(path),))]
    finally:
        db.close()


def summary(path):
    """상세의 폴더 설명(색인에서 센다, 저장하지 않는다): 용량·파일 수, 바로 아래·전체 하위 폴더 수, 종류별 구성, 가장 큰 파일 3개,
    마지막으로 고친 때, 같은 파일(중복 의심 등)인 파일 수. 색인에 없는 폴더면 None."""
    key = exclude.key(path)
    part, values = search.under(path)
    db = index.connect()
    try:
        row = db.execute("select files, size from folders where key = ?", (key,)).fetchone()
        if row is None:
            return None
        lo, hi = values
        search.same_files(db)
        children = db.execute("select count(*) from folders where parent = ?", (key,)).fetchone()[0]
        below = db.execute("select count(*) from folders where key >= ? and key < ?", (lo, hi)).fetchone()[0]
        # +f.kind: 종류 색인을 따라 폴더 밖의 줄까지 찾아가지 않게(search.listing의 종류별 개수와 같다)
        kinds = [list(r) for r in db.execute(f"select f.kind, count(*) n from files f where {part} group by +f.kind order by n desc, f.kind", values)]
        biggest = [dict(r) for r in db.execute(f"select f.path, f.name, f.size from files f where {part} order by f.size desc limit 3", values)]
        latest = db.execute(f"select max(f.modified) from files f where {part}", values).fetchone()[0]
        same = db.execute(f"select count(*) from files f where {part} and {search.DUPLICATE}", values).fetchone()[0]
    finally:
        db.close()
    return {"files": row["files"], "size": row["size"], "children": children, "below": below, "kinds": kinds,
            "biggest": biggest, "latest": latest, "same": same}


def chain(path, folder=False):
    """파일(folder면 그 폴더 자신)이 든 폴더부터 맨 위 폴더까지 거슬러 올라가, 위에서부터의 [{"path", "name"}]. 색인에 없으면 빈 목록."""
    db = index.connect()
    try:
        found = []
        key = exclude.key(path) if folder else os.path.dirname(exclude.key(path))
        while key:
            row = db.execute("select path, name, parent from folders where key = ?", (key,)).fetchone()
            if row is None:
                return []
            found.append({"path": row["path"], "name": row["name"]})
            key = row["parent"]
        return found[::-1]
    finally:
        db.close()
