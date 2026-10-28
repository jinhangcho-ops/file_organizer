"""내가 다는 #태그: 달기·떼기·목록, #태그 검색, 이름 규칙, 폴더·색인 밖 거부, 옮기기·이름 바꾸기·휴지통·다시 색인·다시 인식 뒤에도 남음.
파일은 테스트 안에서 지어내고, 휴지통은 conftest의 fake_trash."""
import os
import shutil

import actions
import exclude
import index
import server
from conftest import build, write


def tags(client, path):
    return client.get("/api/tags", query_string={"path": path}).json["tags"]


def add(client, paths, label):
    return client.post("/api/tags", json={"paths": paths, "label": label})


def names(client, query):
    return sorted(r["name"] for r in client.post("/api/files", json=query).json["rows"])


def test_add_list_and_remove(home, client):
    note = write(home / "메모.txt")
    build(home)
    assert tags(client, note) == []
    assert add(client, [note], "#코딩").json["tags"] == ["코딩"]  # 앞의 #는 떼고 저장
    add(client, [note], "코딩")  # 같은 태그를 두 번 달아도 하나
    assert add(client, [note], " 가계부 ").json["tags"] == ["가계부", "코딩"]  # 가나다 순
    assert tags(client, note) == ["가계부", "코딩"]
    assert client.post("/api/tags/remove", json={"paths": [note], "label": "코딩"}).json["tags"] == ["가계부"]
    assert tags(client, note) == ["가계부"]


def test_search_by_tag(home, client):
    a, b = write(home / "가.txt"), write(home / "나.txt")
    write(home / "코딩.txt")  # 이름에 '코딩'이 든 파일은 #코딩으로 찾지 않는다
    build(home)
    add(client, [a, b], "코딩")
    client.post("/api/tags/remove", json={"paths": [b], "label": "코딩"})
    assert names(client, {"name": "#코딩"}) == ["가.txt"]
    assert names(client, {"tag": "user:코딩"}) == ["가.txt"]
    assert names(client, {"name": "#"}) == ["가.txt", "나.txt", "코딩.txt"]  # #만이면 조건 없음


def test_label_rules(home, client):
    note = write(home / "메모.txt")
    build(home)
    for label in ["", "#", "   ", "가" * 51, "가\n나"]:
        answer = add(client, [note], label)
        assert answer.status_code == 400 and "태그는" in answer.json["error"]
    assert add(client, [note], "가" * 50).status_code == 200


def test_folder_and_unindexed_paths_are_refused(home, client):
    write(home / "폴더" / "a.txt")
    build(home)
    folder = str(home / "폴더")
    answer = add(client, [folder], "코딩")
    assert answer.status_code == 400 and "폴더" in answer.json["error"]
    answer = client.get("/api/tags", query_string={"path": folder})
    assert answer.status_code == 400 and "폴더" in answer.json["error"]
    outside = write(home / "나중.txt")  # 색인한 뒤에 생긴 파일
    assert add(client, [outside], "코딩").status_code == 400
    assert client.get("/api/tags", query_string={"path": outside}).status_code == 400


def test_tags_follow_move_and_rename(home, client):
    note = write(home / "가" / "메모.txt")
    whole = write(home / "통째" / "안.txt")
    (home / "나").mkdir()
    build(home)
    add(client, [note, whole], "코딩")
    actions.move_many([note], str(home / "나"))
    moved = str(home / "나" / "메모.txt")
    assert tags(client, moved) == ["코딩"]
    renamed = actions.rename(moved, "새 이름.txt")
    assert tags(client, renamed) == ["코딩"]
    actions.move_many([str(home / "통째")], str(home / "나"))  # 폴더째 옮기면 그 안의 파일 태그도
    assert tags(client, str(home / "나" / "통째" / "안.txt")) == ["코딩"]
    assert names(client, {"name": "#코딩"}) == ["새 이름.txt", "안.txt"]


def test_tags_stay_after_undo(home, client):
    note = write(home / "가" / "메모.txt")
    (home / "나").mkdir()
    build(home)
    add(client, [note], "코딩")
    client.post("/api/move", json={"paths": [note], "folder": str(home / "나")})
    server.jobs.wait()
    client.post("/api/undo", json={})
    server.jobs.wait()
    assert os.path.exists(note) and tags(client, note) == ["코딩"]


def test_tags_stay_after_trash_and_restore(home, client, fake_trash):
    note = write(home / "메모.txt")
    build(home)
    add(client, [note], "코딩")
    client.post("/api/trash", json={"paths": [note], "confirm": True})
    server.jobs.wait()
    assert fake_trash == [note] and names(client, {"name": "#코딩"}) == []  # 휴지통에 있는 동안은 목록에 없다
    shutil.move(os.path.join(os.path.dirname(home), "휴지통", "1_메모.txt"), note)  # Windows 휴지통의 [복원]처럼 제자리로
    build(home)
    assert tags(client, note) == ["코딩"] and names(client, {"name": "#코딩"}) == ["메모.txt"]


def test_tags_stay_after_rebuild_and_recognize(home, client):
    note = write(home / "사진.png")
    build(home)
    add(client, [note], "코딩")
    build(home)
    assert tags(client, note) == ["코딩"]
    db = index.connect()
    try:  # recognize.recognize_one이 다시 인식할 때 지우는 표들(실제 모델은 돌리지 않는다)
        row_id = db.execute("select id from files where key = ?", (exclude.key(note),)).fetchone()[0]
        for table in ("tags", "faces", "recognized"):
            db.execute(f"delete from {table} where id = ?", (row_id,))
        db.commit()
    finally:
        db.close()
    assert tags(client, note) == ["코딩"]
