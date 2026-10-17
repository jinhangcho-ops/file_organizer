"""검색: 이름, 종류, 고친 날짜 기간, 크기 범위, 파일 안의 글자. 색인을 읽는다."""
import datetime

import index
import kinds

LIMIT = 500  # 한 번에 보여 줄 최대 줄 수
MB = 1024 * 1024
FIELDS = "f.path, f.name, f.kind, f.size, f.created, f.modified, f.accessed, f.downloaded"


def day(text, end=False):
    """'2026-10-17' → 그날 0시(end면 다음 날 0시)의 Unix 초."""
    try:
        start = datetime.datetime.strptime(text.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"날짜는 2026-10-17 모양으로 쓰세요: {text}") from None
    return int((start + datetime.timedelta(days=1 if end else 0)).timestamp())


def number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}는 숫자로 쓰세요: {value}") from None
    if number < 0:
        raise ValueError(f"{name}는 0 이상으로 쓰세요.")
    return number


def find(query):
    """query: {"name", "kinds": [...], "date_from", "date_to", "size_min", "size_max"(MB), "text"} 중 쓴 것만. 고친 시각이 늦은 순."""
    parts, values = [], []
    if (query.get("name") or "").strip():
        parts.append("instr(lower(f.name), lower(?)) > 0")
        values.append(query["name"].strip())
    chosen = query.get("kinds") or []
    unknown = [name for name in chosen if name not in kinds.NAMES]
    if unknown:
        raise ValueError(f"모르는 종류입니다: {', '.join(unknown)}")
    if chosen:
        parts.append(f"f.kind in ({', '.join('?' * len(chosen))})")
        values += chosen
    if (query.get("date_from") or "").strip():
        parts.append("f.modified >= ?")
        values.append(day(query["date_from"]))
    if (query.get("date_to") or "").strip():
        parts.append("f.modified < ?")
        values.append(day(query["date_to"], end=True))
    if str(query.get("size_min") or "").strip():
        parts.append("f.size >= ?")
        values.append(number(query["size_min"], "최소 크기") * MB)
    if str(query.get("size_max") or "").strip():
        parts.append("f.size <= ?")
        values.append(number(query["size_max"], "최대 크기") * MB)

    text = (query.get("text") or "").strip()
    join = snippet = ""
    head = []
    if text:
        join = "join body b on b.rowid = f.id"
        if len(text) >= 3:  # trigram 색인으로 찾는다(대소문자 무시, 글자 그대로).
            parts.append("body match ?")
            values.append('"' + text.replace('"', '""') + '"')
        else:  # 두 글자 이하는 색인을 쓸 수 없어 내용을 훑는다.
            parts.append("instr(lower(b.text), lower(?)) > 0")
            values.append(text)
        snippet = ", substr(b.text, max(1, instr(lower(b.text), lower(?)) - 40), 120) snippet"
        head = [text]
    if not parts:
        raise ValueError("찾을 조건을 하나 이상 쓰세요.")
    sql = f"select {FIELDS}{snippet} from files f {join} where {' and '.join(parts)} order by f.modified desc limit {LIMIT + 1}"
    db = index.connect()
    try:
        rows = [dict(row) for row in db.execute(sql, head + values)]
    finally:
        db.close()
    return {"rows": rows[:LIMIT], "more": len(rows) > LIMIT}
