"""오래된 파일 보관: 400일 전과 100일 전 파일 중 365일이 넘은 것만 골라 보관할 곳의 zip에 넣고(원래 경로 유지),
zip을 확인한 뒤에만 원본을 휴지통으로 보내는지. 이미 압축된 형식은 다시 압축하지 않는지, 설치파일은 빼는지, zip이 잘못되면 원본을 남기는지."""
import os
import time
import zipfile

import pytest

import archive
import search
from conftest import age, build, finish, write


@pytest.fixture
def aged(home, tmp_path):
    files = {
        "old_doc": write(home / "보고" / "오래된보고서.txt", "가나다" * 5000),
        "old_video": write(home / "영상" / "오래된영상.mp4", "x" * 3000),
        "old_exe": write(home / "설치.exe", "exe"),
        "recent": write(home / "보고" / "100일전.txt", "최근"),
    }
    time.sleep(1.5)  # 새 파일의 연 시각을 백신 검사가 바꾼 뒤에 날짜를 바꾼다
    for key in ("old_doc", "old_video", "old_exe"):
        age(files[key], 400)
    age(files["recent"], 100)
    build(home)
    dest = tmp_path / "Archive_ColdStorage"
    dest.mkdir()
    return files, dest


def test_only_files_older_than_365_days_are_archived(home, aged, fake_trash, no_permanent_delete, client):
    files, dest = aged
    preview = client.post("/api/archive/preview", json={"dest": str(dest), "days": 365}).json
    assert preview["files"] == 2 and dict((k, n) for k, n, _ in preview["kinds"]) == {"문서": 1, "동영상": 1}  # 설치파일·100일 전은 빠짐

    started = client.post("/api/archive/run", json={"dest": str(dest), "days": 365, "confirm": True})
    assert started.status_code == 200
    status = finish()
    assert status["error"] == ""
    result = status["result"]
    zips = list(dest.glob("*.zip"))
    assert len(zips) == 1 and result["zip"] == str(zips[0]) and result["files"] == 2
    with zipfile.ZipFile(zips[0]) as z:
        assert z.testzip() is None
        info = {i.filename: i for i in z.infolist()}
    assert set(info) == {archive.arcname(files["old_doc"]), archive.arcname(files["old_video"])}
    assert archive.arcname(files["old_doc"]).endswith("/보고/오래된보고서.txt") and ":" not in archive.arcname(files["old_doc"])
    assert info[archive.arcname(files["old_video"])].compress_type == zipfile.ZIP_STORED  # 동영상은 다시 압축하지 않음
    assert info[archive.arcname(files["old_doc"])].compress_type == zipfile.ZIP_DEFLATED
    assert result["zip_size"] < result["original"]  # 글자 파일은 줄었다

    assert sorted(fake_trash) == sorted([files["old_doc"], files["old_video"]])  # zip 확인 뒤 원본은 휴지통으로(영구 삭제 없음)
    assert result["freed"] == result["original"] == len(("가나다" * 5000).encode()) + 3000  # 휴지통을 비우면 비워질 크기
    assert os.path.exists(files["recent"]) and os.path.exists(files["old_exe"])
    assert {r["name"] for r in search.listing({})["rows"]} == {"100일전.txt", "설치.exe"}


def test_cancel_and_bad_zip_keep_originals(home, aged, fake_trash, monkeypatch, client):
    files, dest = aged
    answer = client.post("/api/archive/run", json={"dest": str(dest), "days": 365})
    assert answer.status_code == 400 and not list(dest.iterdir())  # 확인 없이는 아무것도 하지 않는다

    monkeypatch.setattr(archive, "verify", lambda zip_path, written: False)  # zip이 잘못된 경우
    with pytest.raises(ValueError, match="원본은 그대로"):
        archive.run(str(dest), 365)
    assert all(os.path.exists(files[k]) for k in ("old_doc", "old_video"))
    assert [os.path.splitext(p)[1] for p in fake_trash] == [".zip"]  # 잘못 만든 zip도 휴지통으로(영구 삭제 없음)


def test_archive_folder_itself_is_not_archived(home, aged, fake_trash):
    files, _ = aged
    inside = home / "보관함"
    inside.mkdir()
    old_zip = write(inside / "예전보관.zip", "zip")
    time.sleep(1.5)
    age(old_zip, 500)
    build(home)
    assert old_zip not in {row["path"] for row in archive.candidates(365, str(inside))}


def test_game_and_system_files_from_an_old_index_are_not_archived(home, aged, monkeypatch):
    """예전 판의 색인에 남은 게임 파일, Windows·프로그램 폴더 안의 파일은 보관(원본을 휴지통으로)하지 않는다."""
    import fence
    import games
    files, dest = aged
    before = {row["path"] for row in archive.candidates(365, str(dest))}
    assert files["old_doc"] in before and files["old_video"] in before
    monkeypatch.setattr(games, "launcher_games", lambda: [{"name": "영상 게임", "path": str(home / "영상"), "size": None}])
    games.forget()  # 색인을 다시 하지 않아 색인에는 그 안의 파일이 남아 있다
    assert {row["path"] for row in archive.candidates(365, str(dest))} == {files["old_doc"]}
    monkeypatch.setenv("ProgramFiles", str(home / "보고"))  # 이 폴더를 Program Files로 흉내 낸다
    monkeypatch.setattr(fence.tempfile, "gettempdir", lambda: str(home / "임시"))  # 시험 폴더가 %TEMP% 안이라
    monkeypatch.setattr(fence, "SYSTEM_VARS", ("ProgramFiles",))  # LOCALAPPDATA(시험 폴더가 그 안)는 빼고 본다
    assert archive.candidates(365, str(dest)) == []
