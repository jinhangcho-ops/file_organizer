"""게임 폴더: 색인은 판별만 하고 안을 읽지 않으며(games 표에 남김), 런처 기록(Steam·Epic)을 읽고, 게임·Windows 폴더는 옮기기·휴지통에서 막는다.
다운로드 정리는 프로그램 부품과 그 옆의 exe를 옮기지 않는다. 런처 기록은 지어낸 폴더 구조로만 본다."""
import json
import os

import blacklist
import fence
import games
import organize
from conftest import build, finish, write


def listed(client):
    return {os.path.basename(row["path"]) for row in client.post("/api/files", json={}).json["rows"]}


def test_index_detects_game_folders_and_skips_inside(home, client, monkeypatch):
    monkeypatch.setattr(blacklist, "drive_roots", lambda roots: [str(home)])  # 게임 묶음 이름은 드라이브 바로 아래에서만 보므로
    write(home / "문서" / "메모.txt")
    write(home / "놀이" / "유니티 게임" / "UnityPlayer.dll")
    write(home / "놀이" / "유니티 게임" / "Data" / "level1.assets")
    write(home / "놀이" / "언리얼 게임" / "Game.exe")
    write(home / "놀이" / "언리얼 게임" / "Engine" / "Binaries" / "x.dll")
    write(home / "Riot Games" / "League of Legends" / "LeagueClient.exe")
    write(home / "놀이" / "보통 폴더" / "사진.txt")
    build(home)
    assert listed(client) == {"메모.txt", "사진.txt"}  # 게임 폴더 안의 파일은 목록·중복 의심에 나오지 않는다
    found = sorted(os.path.relpath(g["path"], home) for g in games.found_games())
    assert found == [os.path.join("Riot Games", "League of Legends"), os.path.join("놀이", "언리얼 게임"), os.path.join("놀이", "유니티 게임")]
    names = [g["name"] for g in client.get("/api/games").json["games"]]
    assert names == ["League of Legends", "언리얼 게임", "유니티 게임"]
    client.post("/api/games/measure", json={})
    finish()
    sizes = {g["name"]: g["size"] for g in client.get("/api/games").json["games"]}
    assert sizes["유니티 게임"] == len("내용".encode()) * 2  # 판별만 하고 넘어간 폴더의 크기는 따로 센다
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    assert client.post("/api/games/open", json={"path": str(home / "문서")}).status_code == 400  # 게임이 아닌 폴더는 열지 않는다
    assert client.post("/api/games/open", json={"path": str(home / "Riot Games" / "League of Legends")}).status_code == 200
    assert opened == [str(home / "Riot Games" / "League of Legends")]


def test_launcher_records(tmp_path, monkeypatch):
    steam = tmp_path / "Steam"
    other = tmp_path / "D" / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    (other / "steamapps" / "common" / "Big Game").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n "1"\n {\n  "path"  "%s"\n }\n}\n' % str(other).replace("\\", "\\\\"), encoding="utf-8")
    (other / "steamapps" / "appmanifest_570.acf").write_text(
        '"AppState"\n{\n "appid" "570"\n "name" "Big Game"\n "installdir" "Big Game"\n "SizeOnDisk" "1234"\n "LastPlayed" "1700000000"\n}\n', encoding="utf-8")
    assert games.steam(str(steam)) == [{"name": "Big Game", "launcher": "Steam", "path": str(other / "steamapps" / "common" / "Big Game"),
                                        "size": 1234, "played": 1700000000, "steam_id": "570"}]
    manifests = tmp_path / "Manifests"
    manifests.mkdir()
    (manifests / "a.item").write_text(json.dumps({"DisplayName": "에픽 게임", "InstallLocation": str(tmp_path / "Epic" / "G"), "InstallSize": 99}), encoding="utf-8")
    (manifests / "broken.item").write_text("{", encoding="utf-8")
    assert games.epic(str(manifests)) == [{"name": "에픽 게임", "launcher": "Epic Games", "path": str(tmp_path / "Epic" / "G"), "size": 99, "played": None}]


def test_game_and_system_folders_are_not_moved_or_trashed(home, client, monkeypatch, fake_trash):
    keep = write(home / "보관" / "메모.txt")
    game = home / "게임 모음" / "내 게임"
    write(game / "steam_api64.dll")
    build(home)
    holder = str(home / "게임 모음")  # 게임 폴더를 품은 폴더: 색인에는 있지만 통째로 지우거나 옮기지 않는다
    assert client.post("/api/trash", json={"paths": [holder], "confirm": True}).status_code == 400
    assert client.post("/api/move", json={"paths": [holder], "folder": str(home / "보관")}).status_code == 400
    assert client.post("/api/settings", json={"dest": str(game)}).status_code == 400  # 게임 폴더 안을 놓을 곳으로 고를 수 없다
    monkeypatch.setenv("ProgramFiles", str(home / "보관"))  # 이 폴더를 Program Files로 흉내 낸다
    monkeypatch.setattr(fence.tempfile, "gettempdir", lambda: str(home / "임시"))  # 시험 폴더가 %TEMP% 안이라 임시 폴더를 다른 곳으로
    assert client.post("/api/trash", json={"paths": [keep], "confirm": True}).status_code == 400
    assert fake_trash == []


def test_organize_leaves_program_parts(home, tmp_path):
    source = tmp_path / "다운로드"
    (tmp_path / "정리").mkdir()
    for name in ("setup.exe", "보고서.pdf"):
        write(source / name)
    plan = {os.path.basename(m["from"]) for m in organize.plan(str(source), str(tmp_path / "정리"))}
    assert plan == {"setup.exe", "보고서.pdf"}  # 부품이 없으면 exe도 옮긴다
    for name in ("game.exe", "engine.dll", "settings.ini"):
        write(source / name)
    plan = {os.path.basename(m["from"]) for m in organize.plan(str(source), str(tmp_path / "정리"))}
    assert plan == {"보고서.pdf"}  # 부품이 함께 있으면 exe·부품은 그대로
