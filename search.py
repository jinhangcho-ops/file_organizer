"""가운데 목록: 위치(전체·폴더), 모아 보기(중복 의심·오래 안 연·최근 받은·최근 고친 파일), 검색 조건, 종류, 정렬. 색인을 읽는다.
- 중복 의심: 지문(index.fingerprint)이 같은 파일이 둘 이상.
- 오래 안 연 파일: 연 시각과 고친 시각 중 늦은 쪽이 기간보다 오래됨(기록이 꺼져 있던 동안은 연 시각이 오래된 채로 남기 때문).
- 최근 받은 파일: 다운로드 표시가 있고 만든 시각(받은 시각)이 기간 안.
모아 보기에는 예외(exclude)를 적용한다. 위치·검색에는 적용하지 않는다."""
import datetime
import os
import time

import exclude
import index
import kinds
import settings

LIMIT = 500  # 한 번에 보여 줄 최대 줄 수
DAY = 86400
MB = 1024 * 1024
DUPLICATE = "f.hash in (select hash from files where hash is not null group by hash having count(*) > 1)"
SEEN = "max(f.accessed, f.modified)"
VIEWS = {  # 이름 → (조건, 기간 설정, 날짜 칸)
    "duplicates": (DUPLICATE, None, "f.modified"),
    "stale": (f"{SEEN} < ?", "stale_days", SEEN),
    "downloaded": ("f.downloaded = 1 and f.created >= ?", "recent_days", "f.created"),
    "modified": ("f.modified >= ?", "recent_days", "f.modified"),
}
SORTS = {"name": "lower(f.name)", "kind": "f.kind", "size": "f.size"}  # 그 밖에 "date"는 보기마다 다른 날짜 칸
FIELDS = "f.path, f.name, f.ext, f.kind, f.size, f.created, f.modified, f.accessed, f.downloaded"


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


def under(path):
    """폴더 path 아래(하위 폴더 포함) 파일의 조건과 값."""
    prefix = os.path.join(exclude.key(path), "")
    return "substr(f.key, 1, ?) = ?", [len(prefix), prefix]


def conditions(query, config):
    """query에서 종류를 뺀 조건들(종류별 개수를 셀 때 종류 조건 없이 세려고 나눈다). (조건 목록, 값 목록, 날짜 칸)"""
    parts, values = [], []
    view = query.get("view")
    date = "f.modified"
    if view:
        if view not in VIEWS:
            raise ValueError(f"모르는 목록입니다: {view}")
        condition, setting, date = VIEWS[view]
        parts.append(condition)
        if setting:
            values.append(int(time.time()) - config[setting] * DAY)
        rule, more = exclude.where(exclude.load())
        if rule:
            parts.append(rule)
            values += more
    if query.get("folder"):
        part, more = under(query["folder"])
        parts.append(part)
        values += more
    if (query.get("name") or "").strip():  # 이름이나 폴더 이름
        parts.append("(instr(lower(f.name), lower(?)) > 0 or instr(f.folder, lower(?)) > 0)")
        values += [query["name"].strip()] * 2
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
    if text:
        if len(text) >= 3:  # trigram 색인으로 찾는다(대소문자 무시, 글자 그대로).
            parts.append("f.id in (select rowid from body where body match ?)")
            values.append('"' + text.replace('"', '""') + '"')
        else:  # 두 글자 이하는 색인을 쓸 수 없어 내용을 훑는다.
            parts.append("f.id in (select rowid from body where instr(lower(text), lower(?)) > 0)")
            values.append(text)
    return parts, values, date


def where(parts):
    return " where " + " and ".join(parts) if parts else ""


def listing(query):
    """목록 한 쪽. {"rows", "more", "total", "bytes", "kinds": [[종류, 개수], …](종류 표 순서)}. 줄마다 duplicate·stale 태그와 위치 이름(place)을 붙인다."""
    config = settings.load()
    parts, values, date = conditions(query, config)
    kind = query.get("kind") or ""
    if kind and kind not in kinds.NAMES:
        raise ValueError(f"모르는 종류입니다: {kind}")
    sort = SORTS.get(query.get("sort"), date)
    order = "asc" if query.get("order") == "asc" else "desc"
    stale_since = int(time.time()) - config["stale_days"] * DAY
    picked = parts + (["f.kind = ?"] if kind else [])
    picked_values = values + ([kind] if kind else [])
    text = (query.get("text") or "").strip()
    snippet = ", (select substr(text, max(1, instr(lower(text), lower(?)) - 40), 120) from body where rowid = f.id) snippet" if text else ""
    db = index.connect()
    try:
        counts = dict(db.execute(f"select f.kind, count(*) from files f{where(parts)} group by f.kind", values).fetchall())
        total, size = db.execute(f"select count(*), coalesce(sum(f.size), 0) from files f{where(picked)}", picked_values).fetchone()
        rows = db.execute(
            f"""select {FIELDS}, {date} date, ({DUPLICATE}) duplicate, ({SEEN} < ?) stale{snippet}
                from files f{where(picked)} order by {sort} {order}, f.hash, lower(f.name) limit {LIMIT + 1}""",
            [stale_since] + ([text] if text else []) + picked_values).fetchall()
        places = place_names(db)
    finally:
        db.close()
    rows = [dict(row) | {"place": place(row["path"], places)} for row in rows]
    return {"rows": rows[:LIMIT], "more": len(rows) > LIMIT, "total": total, "bytes": size,
            "kinds": [[name, counts[name]] for name in kinds.NAMES if counts.get(name)]}  # JSON 객체는 키가 정렬되므로 목록으로


def place_names(db):
    """색인한 맨 위 폴더들: [(비교용 key + 구분자, 이름)]"""
    return [(os.path.join(row["key"], ""), row["name"]) for row in db.execute("select key, name from folders where parent is null")]


def place(path, places):
    key = exclude.key(path)
    return next((name for prefix, name in places if key.startswith(prefix)), "")


def counts():
    """사이드바의 숫자: 전체, 모아 보기마다의 파일 수."""
    config = settings.load()
    db = index.connect()
    try:
        found = {"all": db.execute("select count(*) from files").fetchone()[0]}
        for view in VIEWS:
            parts, values, _ = conditions({"view": view}, config)
            found[view] = db.execute(f"select count(*) from files f{where(parts)}", values).fetchone()[0]
        return found
    finally:
        db.close()
