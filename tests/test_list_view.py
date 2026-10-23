"""목록 보기: 드라이브 조건, 폴더별로 묶기(폴더 순서 → 그 안의 정렬), 설치파일 맨 위(묶으면 폴더 안에서), 설정 값 검사. 지어낸 파일만 쓴다."""
import os

import settings
from conftest import build, write


def names(client, **query):
    return [r["name"] for r in client.post("/api/files", json=query).json["rows"]]


def test_drive_condition(home, client):
    write(home / "a.txt")
    build(home)
    drive = os.path.splitdrive(str(home))[0] + "\\"
    other = "Z:\\" if not drive.upper().startswith("Z") else "Y:\\"
    assert names(client, drive=drive) == ["a.txt"] and names(client, drive=other) == []
    assert client.post("/api/files", json={"drive": "C:"}).status_code == 400


def test_group_by_folder_and_pin_installers(home, client):
    write(home / "나" / "b.txt", "xx")
    write(home / "나" / "setup.exe", "x")
    write(home / "가" / "a.txt", "xxx")
    write(home / "가" / "install.msi", "x")
    write(home / "다.txt", "x")
    build(home)
    by_size = {"sort": "size", "order": "desc"}
    assert names(client, **by_size)[:2] == ["a.txt", "b.txt"]  # 기본: 하나로, 크기순
    settings.save({"pin_installers": True})
    assert names(client, **by_size)[:2] == ["install.msi", "setup.exe"] or names(client, **by_size)[:2] == ["setup.exe", "install.msi"]
    settings.save({"pin_installers": False, "list_group": "folder"})
    assert names(client, **by_size) == ["다.txt", "a.txt", "install.msi", "b.txt", "setup.exe"]  # 폴더 경로 순, 그 안은 크기순
    settings.save({"pin_installers": True})
    assert names(client, **by_size) == ["다.txt", "install.msi", "a.txt", "setup.exe", "b.txt"]  # 묶으면 폴더 안에서 맨 위
    rows = client.post("/api/files", json=by_size | {"rows_only": True}).json["rows"]
    assert [r["name"] for r in rows] == ["다.txt", "install.msi", "a.txt", "setup.exe", "b.txt"]  # 정렬만 바꿀 때도 같다


def test_view_settings_are_checked(home, client):
    for bad in ({"drive": "D:"}, {"drive": "DD:\\"}, {"list_group": "type"}, {"pin_installers": "yes"}, {"show_same_name": 1}):
        assert client.post("/api/settings", json=bad).status_code == 400, bad
    saved = client.post("/api/settings", json={"drive": "d:\\", "list_group": "folder", "show_same_name": False}).json["settings"]
    assert saved["drive"] == "D:\\" and saved["list_group"] == "folder" and saved["show_same_name"] is False
