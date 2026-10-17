"""폴더 나무: 왼쪽의 폴더 나무(색인한 맨 위 폴더와 그 아래 폴더, 폴더마다 그 아래 전체 파일 수)와
고른 파일·폴더까지의 경로(맨 위 폴더부터). 색인을 읽는다. 폴더를 펼칠 때 그 폴더의 바로 아래 폴더만 읽는다."""
import os

import exclude
import index
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
