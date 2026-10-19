"""끌어 놓아 옮기기: 나무의 폴더에 놓으면(/api/move에 folder) 실제 경로가 옮겨지는지, 같은 이름이 있으면 덮어쓰지 않고 '이름 (2)'가 되는지,
폴더째 옮기기, 맨 위 폴더·자기 안으로 옮기기를 막는지."""
import os

import search
import tree
from conftest import build, finish, write


def names(rows):
    return {row["name"] for row in rows}


def move(client, paths, folder):
    answer = client.post("/api/move", json={"paths": paths, "folder": folder})
    assert answer.status_code == 200, answer.json
    status = finish()
    assert status["error"] == ""
    return status["result"]


def test_drop_moves_real_files_and_updates_list(home, client):
    a, b = write(home / "a.txt", "가"), write(home / "b.txt", "나")
    os.makedirs(home / "받을곳")
    build(home)
    result = move(client, [a, b], str(home / "받을곳"))
    assert result["moved"] == 2 and result["failed"] == []
    assert sorted(os.listdir(home / "받을곳")) == ["a.txt", "b.txt"] and not os.path.exists(a)
    assert names(search.listing({"folder": str(home / "받을곳")})["rows"]) == {"a.txt", "b.txt"}


def test_same_name_is_never_overwritten(home, client):
    write(home / "받을곳" / "보고서.txt", "먼저 있던 것")
    write(home / "받을곳" / "보고서 (2).txt", "이것도 먼저")
    new = write(home / "보고서.txt", "새로 옮기는 것")
    build(home)
    result = move(client, [new], str(home / "받을곳"))
    assert result["paths"] == [str(home / "받을곳" / "보고서 (3).txt")]
    assert (home / "받을곳" / "보고서.txt").read_text(encoding="utf-8") == "먼저 있던 것"
    assert (home / "받을곳" / "보고서 (2).txt").read_text(encoding="utf-8") == "이것도 먼저"
    assert (home / "받을곳" / "보고서 (3).txt").read_text(encoding="utf-8") == "새로 옮기는 것"


def test_folder_drop_moves_whole_tree(home, client):
    write(home / "묶음" / "안" / "깊은.txt")
    os.makedirs(home / "받을곳")
    build(home)
    result = move(client, [str(home / "묶음")], str(home / "받을곳"))
    assert result["moved"] == 1
    assert (home / "받을곳" / "묶음" / "안" / "깊은.txt").exists() and not (home / "묶음").exists()
    assert [f["name"] for f in tree.children(str(home / "받을곳"))] == ["묶음"]
    assert names(search.listing({"folder": str(home / "받을곳" / "묶음")})["rows"]) == {"깊은.txt"}


def test_folder_into_itself_and_top_folder_are_refused(home, client):
    write(home / "묶음" / "안" / "x.txt")
    build(home)
    result = move(client, [str(home / "묶음")], str(home / "묶음" / "안"))
    assert result["moved"] == 0 and "그 안으로" in result["failed"][0]["error"] and (home / "묶음").exists()
    answer = client.post("/api/move", json={"paths": [str(home)], "folder": str(home / "묶음")})
    assert answer.status_code == 400 and "맨 위 폴더" in answer.json["error"]
