"""모아 보기: 최근 받은 파일, 최근 고친 파일, 오래 안 본 파일. 색인을 읽고 예외를 뺀다.
- 받은 파일: 다운로드 표시가 있는 파일을 만든 시각(받은 시각)으로.
- 안 본 파일: 연 시각과 고친 시각 중 늦은 쪽. 연 시각 기록이 꺼져 있던 동안에는 연 시각이 오래된 채로 남기 때문이다."""
import time

import exclude
import index
import search

DAY = 86400
MODES = {  # 이름 → (조건, 정렬). 조건의 ?는 기준 시각.
    "downloaded": ("f.downloaded = 1 and f.created >= ?", "f.created desc"),
    "modified": ("f.modified >= ?", "f.modified desc"),
    "stale": ("max(f.accessed, f.modified) < ?", "max(f.accessed, f.modified)"),
}


def collect(mode, days):
    if mode not in MODES:
        raise ValueError(f"모르는 목록입니다: {mode}")
    condition, order = MODES[mode]
    rule, values = exclude.where(exclude.load())
    where = condition + (f" and {rule}" if rule else "")
    since = int(time.time()) - days * DAY
    db = index.connect()
    try:
        rows = [dict(row) for row in db.execute(
            f"select {search.FIELDS} from files f where {where} order by {order} limit {search.LIMIT + 1}", [since] + values)]
    finally:
        db.close()
    return {"rows": rows[:search.LIMIT], "more": len(rows) > search.LIMIT}
