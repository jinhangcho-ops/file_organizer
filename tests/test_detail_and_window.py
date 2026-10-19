"""상세의 '내용이 같은 파일'·'이름이 같은 파일', 화면 색 설정, 화면이 그린 제목 줄의 창 버튼(/api/window)."""
import server
import settings
from conftest import build, write


def names(found):
    return {f["path"] for f in found}


def test_same_content_and_same_name(home, client):
    a = write(home / "가" / "보고서.txt", "같은 내용")
    b = write(home / "나" / "사본.txt", "같은 내용")
    c = write(home / "다" / "보고서.TXT", "다른 내용입니다")
    write(home / "라" / "딴것.txt", "같은 길이 아님")
    build(home)
    found = client.get("/api/same?path=" + a).json
    assert names(found["content"]) == {b}  # 지문이 같은 파일
    assert names(found["name"]) == {c}  # 이름만 같은 파일(대소문자 무시)
    assert found["content"][0]["place"] == "root"
    assert client.get("/api/same?path=" + str(home / "없음.txt")).status_code == 400


def test_theme_setting(home, client):
    assert settings.load()["theme"] == "auto"  # 처음에는 Windows 설정을 따른다
    assert client.post("/api/settings", json={"theme": "dark"}).json["settings"]["theme"] == "dark"
    assert client.post("/api/settings", json={"theme": "blue"}).status_code == 400
    assert settings.load()["theme"] == "dark"


def test_window_buttons(home, client, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "window_control", lambda action, data: calls.append((action, data.get("width"))) or {"ok": action})
    for action in ("minimize", "toggle", "bounds", "close"):
        assert client.post("/api/window", json={"action": action}).json == {"ok": action}
    assert client.post("/api/window", json={"action": "place", "x": 0, "y": 0, "width": 1300, "height": 800}).status_code == 200
    assert [a for a, _ in calls] == ["minimize", "toggle", "bounds", "close", "place"] and calls[-1][1] == 1300
    assert client.post("/api/window", json={"action": "explode"}).status_code == 400
    monkeypatch.setattr(server, "window_control", None)
    assert client.post("/api/window", json={"action": "minimize"}).status_code == 400  # 창 없이(브라우저로) 부르면 거부
