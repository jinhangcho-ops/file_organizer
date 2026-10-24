"""캐시 정리: 고른 캐시의 오래된 것만 휴지통으로(영구 삭제 없음). 새것·숨김·잠금 폴더 안은 남기고, 켜져 있는 프로그램의 캐시는 건너뛴다.
캐시 폴더는 환경 변수(TEMP·LOCALAPPDATA·APPDATA)를 임시 폴더로 바꿔 지어낸다. 휴지통은 conftest의 fake_trash."""
import ctypes
import os

import cache
import settings
from conftest import age, finish, write


def places(client):
    client.post("/api/cache/scan", json={})
    return {p["id"]: p for p in finish()["result"]["places"]}


def test_old_cache_goes_to_trash_only_when_picked(home, client, monkeypatch, tmp_path, fake_trash, no_permanent_delete):
    for name in ("TEMP", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setattr(cache, "running", lambda: {"chrome.exe"})
    old_temp = write(tmp_path / "TEMP" / "old.tmp")
    old_folder = write(tmp_path / "TEMP" / "설치 찌꺼기" / "a.bin")
    new_temp = write(tmp_path / "TEMP" / "new.tmp")
    fresh_inside = write(tmp_path / "TEMP" / "쓰는 중" / "old.bin")
    write(tmp_path / "TEMP" / "쓰는 중" / "new.bin")
    hidden = write(tmp_path / "TEMP" / "숨김.tmp")
    edge = write(tmp_path / "LOCALAPPDATA" / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache" / "Cache_Data" / "data_1")
    chrome = write(tmp_path / "LOCALAPPDATA" / "Google" / "Chrome" / "User Data" / "Profile 1" / "Cache" / "Cache_Data" / "data_1")
    for path in (old_temp, old_folder, os.path.dirname(old_folder), fresh_inside, hidden, edge, chrome):
        age(path, 30)
    ctypes.windll.kernel32.SetFileAttributesW(hidden, 0x2)
    found = places(client)
    assert (found["temp"]["count"], found["edge"]["count"], found["chrome"]["running"]) == (2, 1, True)
    assert not found["discord"]["found"]
    assert client.post("/api/cache/clean", json={"ids": ["temp", "edge", "chrome"]}).status_code == 400  # 확인 없이는 안 보낸다
    assert client.post("/api/cache/clean", json={"ids": ["nope"], "confirm": True}).status_code == 400
    client.post("/api/cache/clean", json={"ids": ["temp", "chrome"], "confirm": True})
    result = finish()["result"]
    assert sorted(fake_trash) == sorted([old_temp, os.path.dirname(old_folder)])  # 고르지 않은 Edge, 켜져 있는 Chrome은 그대로
    assert result["trashed"] == 2 and result["failed"][0]["from"] == "Chrome"
    assert all(os.path.exists(p) for p in (new_temp, fresh_inside, hidden, edge, chrome))


def test_cache_choices_are_remembered(home, client):
    assert client.post("/api/settings", json={"cache_skip": ["edge", "discord"]}).status_code == 200
    assert settings.load()["cache_skip"] == ["edge", "discord"]
    assert client.post("/api/settings", json={"cache_skip": ["C:\\Windows"]}).status_code == 400
    assert client.post("/api/settings", json={"cache_days": 0}).status_code == 400
