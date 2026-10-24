"""게임 폴더 key 기억(빠르게), 왼쪽 폴더 목록의 게임 용량, 런처 게임의 센 크기 남기기, 캐시 정리의 위험한 TEMP·Firefox 캐시,
되돌리기의 일부만 옮김. 모두 지어낸 폴더로 본다."""
import os
from pathlib import Path

import actions
import cache
import exclude
import games
import organize
import tree
from conftest import age, build, finish, write


def test_game_keys_are_read_once_and_refreshed_after_indexing(home, monkeypatch):
    calls = []
    real = games.found_games
    monkeypatch.setattr(games, "found_games", lambda: calls.append(1) or real())
    games.forget()  # 설정을 저장할 때(home) 이미 한 번 읽었다
    games.keys(), games.keys(), games.keys()
    assert len(calls) == 1  # 휴지통·폴더 비우기가 항목마다 물어도 한 번만 읽는다
    write(home / "새 게임" / "steam_api64.dll")
    build(home)
    assert exclude.key(str(home / "새 게임")) in games.keys()  # 색인이 게임 표를 고치면 바로 다시 읽는다


def test_game_folders_show_with_size_in_the_folder_list(home, monkeypatch):
    write(home / "문서" / "메모.txt", "12345")
    big = home / "놀이" / "큰 게임"
    write(big / "steam_api64.dll")
    write(home / "Riot Games" / "League of Legends" / "a.exe")
    monkeypatch.setattr(games, "launcher_games", lambda: [{"name": "큰 게임", "launcher": "Steam", "path": str(big), "size": 10_000, "played": None}])
    build(home)
    top = tree.places()[0]
    assert top["size"] == 5 + 10_000  # 안을 읽지 않은 게임 폴더의 크기도 맨 위 폴더 용량에 든다
    names = {row["name"]: row["size"] for row in tree.children(str(home))}
    assert names["놀이"] == 10_000 and "Riot Games" in names
    assert [row["name"] for row in tree.children(str(home / "Riot Games"))] == ["League of Legends"]


def test_measured_size_of_launcher_game_survives_next_index(home, client, tmp_path, monkeypatch):
    gog = tmp_path / "GOG Games" / "옛 게임"
    write(gog / "game.bin", "123")
    monkeypatch.setattr(games, "launcher_games", lambda: [{"name": "옛 게임", "launcher": "GOG", "path": str(gog), "size": None, "played": None}])
    client.post("/api/games/measure", json={})
    finish()
    build(home)
    assert {g["name"]: g["size"] for g in client.get("/api/games").json["games"]}["옛 게임"] == 3


def test_cache_skips_temp_that_points_at_home_or_a_drive(home, monkeypatch, tmp_path):
    user = tmp_path / "사용자"
    old = write(user / "문서" / "보고서.txt")
    age(old, 30)
    age(str(user / "문서"), 30)
    monkeypatch.setattr(cache, "Path", type("P", (), {"home": staticmethod(lambda: Path(user))}))
    monkeypatch.setenv("TEMP", str(user))
    assert cache.old_items([r"%TEMP%"], 7) == []  # TEMP가 사용자 폴더면 건너뛴다
    monkeypatch.setattr(cache, "Path", type("P", (), {"home": staticmethod(lambda: Path("Z:/사용자"))}))  # 사용자 폴더가 다른 디스크
    monkeypatch.setenv("TEMP", os.path.splitdrive(str(tmp_path))[0] + "\\")
    assert cache.folders([r"%TEMP%"]) == []  # 드라이브 맨 위(사용자 폴더가 없는 디스크라도)는 건너뛴다


def test_firefox_cache_entries_are_found(home, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LOCALAPPDATA"))
    entries = tmp_path / "LOCALAPPDATA" / "Mozilla" / "Firefox" / "Profiles" / "x.default" / "cache2" / "entries"
    old = write(entries / "A1B2")
    write(entries / "C3D4")  # 방금 쓴 캐시(그래서 entries 폴더는 늘 새것)
    age(old, 30)
    firefox = next(p for p in cache.PLACES if p[0] == "firefox")
    assert [path for path, _ in cache.old_items(firefox[4], 7)] == [old]


def test_undo_records_a_partial_folder_move(home, monkeypatch, tmp_path):
    before = tmp_path / "가" / "폴더"
    write(before / "a.txt")
    after = tmp_path / "나" / "폴더"
    os.makedirs(after.parent)
    os.rename(before, after)

    def partial(src, dst):
        os.rename(src, dst)
        raise organize.Partial("옮기는 동안 바뀐 파일은 남겼습니다.")

    monkeypatch.setattr(organize, "move_folder", partial)
    result = actions.move_back([(str(before), str(after))])
    assert result["moved"] == 1 and result["paths"] == [str(before)] and len(result["failed"]) == 1
