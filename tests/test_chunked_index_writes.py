"""큰 폴더를 옮기거나 지울 때 색인 반영을 GONE_CHUNK씩 나눠 저장하는지(그동안 쓰기 잠금을 오래 쥐지 않게).
옮긴 뒤의 결과는 한 번에 할 때와 같아야 한다. 파일은 모두 지어낸다."""
import os

import index
from conftest import build, write


def count_saves(monkeypatch):
    saves = []
    real = index.save_now
    monkeypatch.setattr(index, "save_now", lambda db, force=False: (saves.append(force), real(db, force)))
    monkeypatch.setattr(index, "GONE_CHUNK", 10)
    return saves


def test_moving_a_big_folder_saves_in_chunks(home, monkeypatch):
    for n in range(35):
        write(home / "큰 폴더" / f"안{n % 3}" / f"{n}.txt", str(n))
    build(home)
    saves = count_saves(monkeypatch)
    os.rename(home / "큰 폴더", home / "옮긴 폴더")
    index.moved([(str(home / "큰 폴더"), str(home / "옮긴 폴더"))])
    assert len(saves) >= 4  # 파일 35줄을 10줄씩: 저장할 기회가 네 번 이상
    db = index.connect()
    try:
        paths = sorted(row[0] for row in db.execute("select path from files"))
        assert paths == sorted(str(home / "옮긴 폴더" / f"안{n % 3}" / f"{n}.txt") for n in range(35))
        assert not db.execute("select 1 from folders where path like ?", (str(home / "큰 폴더") + "%",)).fetchone()
    finally:
        db.close()


def test_forgetting_a_big_folder_saves_in_chunks(home, monkeypatch):
    for n in range(25):
        write(home / "지울 폴더" / f"{n}.txt", str(n))
    write(home / "남길 것.txt")
    build(home)
    saves = count_saves(monkeypatch)
    index.removed([str(home / "지울 폴더")])
    assert len(saves) >= 3
    db = index.connect()
    try:
        assert [row[0] for row in db.execute("select path from files")] == [str(home / "남길 것.txt")]
    finally:
        db.close()
