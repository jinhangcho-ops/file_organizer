"""정렬: 정렬만 바꿀 때는 줄만 받기(rows_only)가 같은 줄을 같은 순서로 주는지, 정렬과 폴더 조건이 B-트리 색인을 타는지(실행 계획).
지어낸 파일만 쓴다."""
import os

import index
import search
from conftest import build, write


def test_rows_only_gives_the_same_rows_without_counts(home, client):
    for n, name in enumerate(["다람쥐.txt", "apple.txt", "Banana.txt", "가방.txt", "zebra.txt"]):
        write(home / "가" / name, "x" * (n + 1))
    write(home / "나" / "다른 곳.txt", "y")
    build(home)
    for sort, order in (("name", "asc"), ("name", "desc"), ("size", "desc"), ("date", "asc")):
        query = {"sort": sort, "order": order, "folder": str(home / "가")}
        full = client.post("/api/files", json=query).json
        rows = client.post("/api/files", json=query | {"rows_only": True}).json
        assert set(rows) == {"rows", "more"} and rows["rows"] == full["rows"], (sort, order)
    names = [r["name"] for r in client.post("/api/files", json={"sort": "name", "order": "asc", "folder": str(home / "가")}).json["rows"]]
    assert names == ["apple.txt", "Banana.txt", "zebra.txt", "가방.txt", "다람쥐.txt"]  # A-Z(대소문자 무시) 다음 ㄱ-ㅎ


def test_sorts_and_folder_condition_use_indexes(home):
    build(home)
    db = index.connect()
    for sort in ("lower(f.name) asc", "f.size desc", "f.modified desc", "max(f.accessed, f.modified) desc"):
        plan = " ".join(r[3] for r in db.execute(f"explain query plan select f.id from files f order by {sort}, f.id limit 501"))
        assert "INDEX files_" in plan, (sort, plan)
    part, values = search.under(str(home))
    plan = " ".join(r[3] for r in db.execute(f"explain query plan select f.id from files f where {part}", values))
    assert "sqlite_autoindex_files" in plan or "INDEX" in plan, plan  # 경로(key) 색인으로 범위를 찾는다
    db.close()


def test_folder_range_is_exact(home, client):
    write(home / "ab" / "안.txt")
    write(home / "ab]" / "괄호.txt")  # 범위의 끝 글자(])가 든 이름의 옆 폴더
    write(home / "abc" / "옆.txt")
    write(home / "ab" / "아래" / "깊이.txt")
    build(home)
    names = sorted(r["name"] for r in client.post("/api/files", json={"folder": str(home / "ab")}).json["rows"])
    assert names == ["깊이.txt", "안.txt"], names
    assert os.path.isdir(home / "ab]")
