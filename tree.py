"""폴더 구조: 사이드바의 위치(색인한 맨 위 폴더와 그 아래 전체 파일 수)와 상세의 경로(맨 위 폴더부터 파일이 든 폴더까지). 색인을 읽는다."""
import os

import exclude
import index
import settings


def places():
    """색인한 맨 위 폴더들을 설정에 적은 순서로(놓을 곳은 끝에). [{"path", "name", "files", "size"}]"""
    order = [exclude.key(path) for path in index.roots(settings.load())]
    db = index.connect()
    try:
        rows = [dict(row) for row in db.execute("select key, path, name, files, size from folders where parent is null")]
    finally:
        db.close()
    rows.sort(key=lambda row: order.index(row["key"]) if row["key"] in order else len(order))
    return [{k: row[k] for k in ("path", "name", "files", "size")} for row in rows]


def chain(path):
    """파일(또는 폴더)이 든 폴더부터 맨 위 폴더까지 거슬러 올라가, 위에서부터의 [{"path", "name"}]. 색인에 없으면 빈 목록."""
    db = index.connect()
    try:
        found = []
        key = os.path.dirname(exclude.key(path))
        while key:
            row = db.execute("select path, name, parent from folders where key = ?", (key,)).fetchone()
            if row is None:
                return []
            found.append({"path": row["path"], "name": row["name"]})
            key = row["parent"]
        return found[::-1]
    finally:
        db.close()
