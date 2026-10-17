"""나무 보기: 폴더 하나의 하위 폴더(그 아래 전체의 파일 수·용량)와 파일. 색인을 읽는다. 펼칠 때 그 폴더만 불러온다."""
import os

import exclude
import index

MAX_FILES = 1000  # 한 폴더에서 보여 줄 최대 파일 수


def children(path=None):
    """path가 없으면 색인한 맨 위 폴더들."""
    db = index.connect()
    try:
        if not path:
            folders = db.execute("select path, name, files, size from folders where parent is null order by path").fetchall()
            return {"folders": [dict(row) for row in folders], "files": [], "more": 0}
        key = exclude.key(path)
        folders = db.execute("select path, name, files, size from folders where parent = ? order by name", (key,)).fetchall()
        files = db.execute(f"select path, name, kind, size, modified from files where folder = ? order by name limit {MAX_FILES + 1}",
                           (key,)).fetchall()
        total = db.execute("select count(*) from files where folder = ?", (key,)).fetchone()[0]
        return {"folders": [dict(row) for row in folders], "files": [dict(row) for row in files[:MAX_FILES]],
                "more": max(0, total - MAX_FILES)}
    finally:
        db.close()


def chain(path):
    """파일(또는 폴더)이 든 폴더부터 맨 위 폴더까지 거슬러 올라가, 위에서부터의 폴더 경로 목록. 색인에 없으면 빈 목록."""
    db = index.connect()
    try:
        found = []
        key = os.path.dirname(exclude.key(path))
        while key:
            row = db.execute("select path, parent from folders where key = ?", (key,)).fetchone()
            if row is None:
                return []
            found.append(row["path"])
            key = row["parent"]
        return found[::-1]
    finally:
        db.close()
