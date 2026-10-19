"""칸 너비(왼쪽 폴더 목록, 오른쪽 상세): 범위 안의 정수만 저장하고, 0은 기본으로 돌아가며, 화면은 범위를 /api/state로 받는다."""
import settings


def test_pane_widths_saved_and_checked(home, client):
    state = client.get("/api/state").json
    assert state["panes"] == settings.PANES
    assert (state["settings"]["pane_left"], state["settings"]["pane_detail"]) == (0, 0)  # 처음에는 기본
    assert client.post("/api/settings", json={"pane_left": 520, "pane_detail": 300}).status_code == 200
    assert (settings.load()["pane_left"], settings.load()["pane_detail"]) == (520, 300)
    for bad in (199, 701, "300", 300.5, True, None):
        assert client.post("/api/settings", json={"pane_left": bad}).status_code == 400
    assert client.post("/api/settings", json={"pane_detail": 259}).status_code == 400
    assert settings.load()["pane_left"] == 520  # 거부한 값은 저장하지 않는다
    assert client.post("/api/settings", json={"pane_left": 0}).json["settings"]["pane_left"] == 0  # 두 번 눌러 기본으로
