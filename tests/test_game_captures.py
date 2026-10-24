"""게임 캡처 정리: Game Bar·NVIDIA·AMD·Steam의 캡처를 사진\\스크린샷\\게임, 동영상\\게임 녹화\\게임으로 옮기고 Ctrl+Z로 되돌린다.
다운로드 정리도 게임 캡처 이름이면 그리로 보낸다. 사진·동영상·Steam 폴더는 conftest의 home이 임시 폴더로 바꾼다."""
import os

import captures
import games
import organize
from conftest import finish, write


def test_game_names_from_capture_file_names():
    assert captures.game_from_name("Elden Ring 2026-10-24 14-33-00.png") == "Elden Ring"
    assert captures.game_from_name("Halo Infinite 10_24_2026 2_33_05 PM.mp4") == "Halo Infinite"
    assert captures.game_from_name("Cyberpunk 2077 Screenshot 2026.10.24 - 14.33.00.01.png") == "Cyberpunk 2077"
    assert captures.game_from_name("Cyberpunk 2077 2026.10.24 - 14.33.00.02.DVR.mp4") == "Cyberpunk 2077"
    assert captures.game_from_name("보고서 2026-10-24.pdf") is None
    assert captures.safe_name('A: "B"?') == "A B"
    assert captures.safe_name("...") == captures.UNKNOWN


def test_captures_move_to_game_folders_and_undo(home, client, monkeypatch, tmp_path):
    base = captures.folders()
    videos, pictures = base["videos"], base["pictures"]
    bar_shot = write(os.path.join(videos, "Captures", "Elden Ring 2026-10-24 14-33-00.png"))
    bar_clip = write(os.path.join(videos, "Captures", "Elden Ring 2026-10-24 14-40-00.mp4"))
    other = write(os.path.join(videos, "Captures", "내 영상.mp4"))  # 게임 캡처 이름이 아니면 그대로
    nvidia = write(os.path.join(videos, "Cyberpunk 2077", "Cyberpunk 2077 2026.10.24 - 14.33.00.02.DVR.mp4"))
    amd = write(os.path.join(videos, "Radeon ReLive", "Apex Legends", "Replay_2026.10.24.mp4"))
    steam = write(os.path.join(base["steam"], "userdata", "123", "760", "remote", "570", "screenshots", "20261024_1.jpg"))
    unknown = write(os.path.join(base["steam"], "userdata", "123", "760", "remote", "999", "screenshots", "20261024_2.jpg"))
    monkeypatch.setattr(games, "launcher_games", lambda: [{"name": "Dota 2", "steam_id": "570", "path": str(tmp_path / "x")}])
    preview = client.post("/api/captures/preview", json={}).json
    assert preview["count"] == 6
    assert {(g["game"], g["source"], g["count"]) for g in preview["groups"]} == {
        ("Elden Ring", "Xbox Game Bar", 2), ("Cyberpunk 2077", "NVIDIA", 1), ("Apex Legends", "AMD", 1), ("Dota 2", "Steam", 1), ("Steam 게임 999", "Steam", 1)}
    client.post("/api/captures/run", json={})
    status = finish()
    assert status["result"]["moved"] == 6 and status["result"]["failed"] == []
    assert os.path.exists(os.path.join(pictures, "스크린샷", "Elden Ring", os.path.basename(bar_shot)))
    assert os.path.exists(os.path.join(videos, "게임 녹화", "Elden Ring", os.path.basename(bar_clip)))
    assert os.path.exists(os.path.join(videos, "게임 녹화", "Cyberpunk 2077", os.path.basename(nvidia)))
    assert os.path.exists(os.path.join(videos, "게임 녹화", "Apex Legends", os.path.basename(amd)))
    assert os.path.exists(os.path.join(pictures, "스크린샷", "Dota 2", os.path.basename(steam)))
    assert os.path.exists(os.path.join(pictures, "스크린샷", "Steam 게임 999", os.path.basename(unknown)))
    assert os.path.exists(other)
    assert client.post("/api/captures/preview", json={}).json["count"] == 0  # 옮긴 것은 다시 옮기지 않는다
    client.post("/api/undo", json={})
    finish()
    assert all(os.path.exists(p) for p in (bar_shot, bar_clip, nvidia, amd, steam, unknown))


def test_download_organize_sends_game_captures_to_game_folders(home, tmp_path):
    source, dest = tmp_path / "다운로드", tmp_path / "정리"
    dest.mkdir()
    write(source / "Elden Ring 2026-10-24 14-33-00.png")
    write(source / "보고서.pdf")
    moves = {os.path.basename(m["from"]): m["to"] for m in organize.plan(str(source), str(dest))}
    assert moves["Elden Ring 2026-10-24 14-33-00.png"] == os.path.join(captures.folders()["pictures"], "스크린샷", "Elden Ring", "Elden Ring 2026-10-24 14-33-00.png")
    assert moves["보고서.pdf"].startswith(str(dest))
