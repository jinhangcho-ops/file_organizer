"""휴지통 안전: 파일(여러 개)과 폴더를 지울 때 영구 삭제 함수 없이 모두 send2trash를 거치는지, 확인하지 않으면 그대로 남는지,
휴지통이 없거나 꺼진 곳·휴지통보다 큰 것은 보내지 않는지(send2trash가 경고 없이 영구 삭제할 수 있는 경우)."""
import os

import pytest

import search
import trash
from conftest import build, finish, write


def names(rows):
    return {row["name"] for row in rows}


@pytest.fixture
def files(home):
    paths = [write(home / f"지울{n}.txt") for n in range(3)]
    write(home / "프로젝트_백업" / "안" / "자료.txt")
    write(home / "남길.txt")
    build(home)
    return paths


def test_files_and_folder_go_only_to_send2trash(home, files, fake_trash, no_permanent_delete, client):
    folder = str(home / "프로젝트_백업")
    started = client.post("/api/trash", json={"paths": files + [folder], "confirm": True})
    assert started.status_code == 200
    status = finish()
    assert status["error"] == ""
    assert status["result"] == {"trashed": 4, "failed": []}
    assert fake_trash == files + [folder]  # 모두 send2trash로(그리고 영구 삭제 함수는 한 번도 불리지 않았다: no_permanent_delete)
    assert not any(os.path.exists(p) for p in files) and not os.path.exists(folder)
    assert names(search.listing({})["rows"]) == {"남길.txt"}  # 색인에서도 빠짐(폴더는 그 아래 전부)


def test_cancelled_confirm_keeps_everything(home, files, fake_trash, client):
    for body in ({"paths": files}, {"paths": files, "confirm": False}, {"paths": files, "confirm": "yes"}):
        answer = client.post("/api/trash", json=body)
        assert answer.status_code == 400 and "확인한 뒤에만" in answer.json["error"]
    assert fake_trash == []
    assert all(os.path.exists(p) for p in files)


def test_top_folder_and_unknown_paths_are_refused(home, files, fake_trash, client):
    assert client.post("/api/trash", json={"paths": [str(home)], "confirm": True}).status_code == 400  # 색인한 맨 위 폴더
    assert client.post("/api/trash", json={"paths": [str(home / "없는.txt")], "confirm": True}).status_code == 400
    assert fake_trash == [] and os.path.exists(home)


@pytest.mark.parametrize("setup, reason", [
    (lambda m: m.setattr(trash.drives, "drive_type", lambda path: 4), "휴지통이 없는 곳"),  # 네트워크
    (lambda m: m.setattr(trash.drives, "drive_type", lambda path: 2), "휴지통이 없는 곳"),  # 이동식(USB)
    (lambda m: m.setattr(trash, "recycle_settings", lambda path: (True, None)), "휴지통을 쓰지 않도록"),
    (lambda m: m.setattr(trash, "recycle_settings", lambda path: (False, 1)), "휴지통 최대 크기보다"),
])
def test_places_that_would_delete_permanently_are_refused(home, files, fake_trash, no_permanent_delete, monkeypatch, setup, reason):
    setup(monkeypatch)
    sent, refused = trash.to_trash(files)
    assert sent == [] and fake_trash == []
    assert all(reason in why for _, why in refused) and len(refused) == len(files)
    assert all(os.path.exists(p) for p in files)
