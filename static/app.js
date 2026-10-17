"use strict";
// 화면: 왼쪽 사이드바(위치, 정리하기·설정), 가운데 목록(또는 정리·설정), 오른쪽 상세. 서버는 server.py.

const $ = (id) => document.getElementById(id);
let state = null;                 // /api/state의 답
let page = "files";               // files, organize, setup
let scope = {all: true};          // 목록의 범위: {all} | {folder, name}
let filter = "";                  // 고른 필터 칩(VIEWS의 이름, "" = 없음)
let kind = "";                    // 고른 종류 칩("" = 전체)
let sort = "date", order = "desc";
let more = {};                    // [조건]의 값
let picked = null;                // 상세에 보이는 파일(목록의 줄)
let menuPath = null;              // 오른쪽 클릭한 파일
let source = "", dest = "";       // 정리할 곳, 놓을 곳
let listed = 0;                   // 늦게 온 목록 응답을 버리기 위한 번호

const VIEWS = {  // 목록의 필터 칩이자 설정 화면 맨 위의 카드(이 순서). mark: 정리할 거리라 카드에 노란 점을 붙인다.
  duplicates: {title: "중복 의심", date: "수정한 날짜", mark: true},
  downloaded: {title: "최근 다운로드", date: "받은 날짜"},
  modified: {title: "최근 수정", date: "수정한 날짜"},
  stale: {title: () => `${state.settings.stale_days}일 넘게 안 본 파일`, date: "마지막으로 연 날짜", mark: true},
};
// 확장자 배지 색(라이트·다크 같음, 글자는 흰색)
const BADGES = [
  [["xls", "xlsx", "xlsm", "csv", "tsv", "ods"], "#2e7d4f"],
  [["pdf"], "#c4473d"],
  [["doc", "docx", "hwp", "hwpx", "txt", "md", "rtf", "odt"], "#3d64c8"],
  [["ppt", "pptx", "odp"], "#d0712d"],
  [["jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "svg", "tif", "tiff", "ico", "raw", "psd"], "#8d5b12"],
  [["mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m4v", "ts"], "#26808a"],
  [["mp3", "wav", "flac", "aac", "m4a", "ogg", "wma"], "#b0467c"],
  [["zip", "7z", "rar", "tar", "gz", "bz2", "xz", "alz", "egg"], "#7a55c4"],
  [["exe", "msi", "msix", "appx", "apk", "iso", "dmg"], "#4b5563"],
];

async function api(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({error: `응답을 읽지 못했습니다 (${response.status})`}));
  if (!response.ok) throw new Error(body.error || String(response.status));
  return body;
}

// 요청하는 동안 버튼을 잠그고, 실패하면 messageId에 문구를 보여 준다.
async function busy(button, messageId, work) {
  button.disabled = true;
  try {
    await work();
  } catch (e) {
    say(messageId, e.message, "error");
  } finally {
    button.disabled = false;
  }
}

function say(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = "message " + kind;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

const lower = (path) => path.toLowerCase();
const two = (n) => String(n).padStart(2, "0");

function size(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes || 0, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + units[i];
}

function shortDate(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000);
  return d.getFullYear() === new Date().getFullYear() ? `${d.getMonth() + 1}월 ${d.getDate()}일` : `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
}

function fullDate(seconds) {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}. ${two(d.getMonth() + 1)}. ${two(d.getDate())}. ${two(d.getHours())}:${two(d.getMinutes())}`;
}

function badge(ext, big = false) {
  const name = (ext || "").replace(".", "").toLowerCase();
  const color = (BADGES.find(([exts]) => exts.includes(name)) || [null, "#6b7280"])[1];
  const node = el("div", {className: "badge" + (big ? " big" : ""), textContent: (name || "?").slice(0, 4).toUpperCase()});
  node.style.background = color;
  return node;
}

const extOf = (name) => (name.includes(".") ? name.slice(name.lastIndexOf(".")) : "");
const viewTitle = (view) => (typeof VIEWS[view].title === "function" ? VIEWS[view].title() : VIEWS[view].title);

// ---------- 상태·사이드바 ----------
async function loadState() {
  state = await api("/api/state");
  source = source || state.settings.source;
  dest = dest || state.settings.dest;
  showSide();
  showIndex(state.index);
  showOrganize();
  showSetup();
}

function sideButton(label, count, on, onClick) {
  const button = el("button", {className: on ? "on" : ""}, el("span", {textContent: label}), el("span", {className: "n", textContent: count.toLocaleString()}));
  button.addEventListener("click", onClick);
  return el("li", {}, button);
}

function showSide() {
  const files = page === "files";
  $("places").replaceChildren(
    sideButton("모든 파일", state.counts.all, files && scope.all, () => openScope({all: true})),
    ...state.places.map((p) => sideButton(p.name, p.files, files && scope.folder && lower(scope.folder) === lower(p.path),
      () => openScope({folder: p.path, name: p.name}))));
  document.querySelectorAll(".side-foot [data-page]").forEach((b) => b.classList.toggle("on", b.dataset.page === page));
}

function showIndex(status) {
  $("index-status").textContent = status.running
    ? `색인하는 중… ${status.done.toLocaleString()}개 확인`
    : status.error ? "색인 실패: " + status.error
    : status.finished ? `색인 갱신 ${fullDate(status.finished)}` : "";
  if (status.stopped && !status.running) $("index-status").textContent = `색인을 멈췄습니다 (${status.done.toLocaleString()}개까지 반영)`;
  $("index-progress").textContent = status.running ? status.current : "";
  $("reindex").disabled = status.running;
  document.querySelectorAll("[data-stop]").forEach((b) => b.classList.toggle("hidden", !status.running));
}

document.querySelectorAll("[data-stop]").forEach((button) => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    showIndex(await api("/api/index/stop", {}));
  } finally {
    button.disabled = false;
  }
}));

async function pollIndex() {
  const status = await api("/api/index");
  showIndex(status);
  if (status.running) {
    setTimeout(pollIndex, 1000);
  } else {
    await loadState();
    await loadFiles();
  }
}

function showPage(name) {
  page = name;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-" + name));
  showSide();
}

document.querySelectorAll(".side-foot [data-page]").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));

// ---------- 목록 ----------
function openScope(next, view = "") {
  scope = next;
  filter = view;
  kind = "";
  showPage("files");
  loadFiles();
}

async function loadFiles() {
  const number = ++listed;
  const query = {...more, name: $("name").value, kind, sort, order};
  if (filter) query.view = filter;
  if (scope.folder) query.folder = scope.folder;
  $("title").textContent = scope.folder ? scope.name : "모든 파일";
  try {
    const found = await api("/api/files", query);
    if (number !== listed) return;
    $("list-error").classList.add("hidden");
    showFiles(found);
  } catch (e) {
    $("list-error").textContent = e.message;
    $("list-error").classList.remove("hidden");
  }
}

function showFiles(found) {
  $("summary").textContent = `${found.total.toLocaleString()}개 항목, 합계 ${size(found.bytes)}` +
    (found.more ? ` · 앞의 ${found.rows.length.toLocaleString()}개만 보입니다` : "");
  const viewCounts = Object.fromEntries(found.views);
  $("filters").replaceChildren(...Object.keys(VIEWS).map((view) => {  // 설정 화면의 카드와 같은 순서
    const n = viewCounts[view];
    const button = el("button", {className: filter === view ? "on" : ""}, viewTitle(view), el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => { filter = filter === view ? "" : view; loadFiles(); });
    return button;
  }));
  const all = found.kinds.reduce((sum, [, n]) => sum + n, 0);
  $("chips").replaceChildren(...[["", "전체", all], ...found.kinds.map(([k, n]) => [k, k, n])].map(([value, label, n]) => {
    const button = el("button", {className: kind === value ? "on" : ""}, label, el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => { kind = value; loadFiles(); });
    return button;
  }));
  $("date-label").textContent = filter ? VIEWS[filter].date : "수정한 날짜";
  document.querySelectorAll("#files th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === sort);
    th.dataset.arrow = th.dataset.sort === sort ? (order === "asc" ? " ↑" : " ↓") : "";
    const label = th.querySelector("span") || th;
    label.textContent = label.textContent.replace(/ [↑↓]$/, "") + th.dataset.arrow;
  });
  const stale = `${state.settings.stale_days}일 넘게 안 열었어요`;
  $("files").tBodies[0].replaceChildren(...found.rows.map((row) => {
    const sub = el("div", {className: "file-sub"}, el("span", {textContent: row.place}),
      row.duplicate ? el("span", {className: "tag", textContent: "중복 의심"}) : null,
      row.stale ? el("span", {className: "tag", textContent: stale}) : null);
    const text = el("div", {className: "file-text"}, el("div", {className: "file-name", textContent: row.name}), sub,
      row.snippet ? el("div", {className: "snippet", textContent: "… " + row.snippet.replace(/\s+/g, " ") + " …"}) : null);
    const tr = el("tr", {className: picked && lower(picked.path) === lower(row.path) ? "picked" : ""},
      el("td", {}, el("div", {className: "file"}, badge(row.ext), text)),
      el("td", {className: "muted", textContent: row.kind}),
      el("td", {className: "muted", textContent: size(row.size)}),
      el("td", {className: "muted", textContent: shortDate(row.date)}));
    tr.row = row;
    return tr;
  }));
  $("empty").textContent = state.counts.all ? "조건에 맞는 파일이 없습니다." : "아직 색인이 없습니다. [설정]에서 [색인 갱신]을 누르세요.";
  $("empty").classList.toggle("hidden", found.rows.length > 0);
}

let typing = null;
$("name").addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(loadFiles, 250);
});

$("more-toggle").addEventListener("click", (e) => {
  $("more").classList.toggle("hidden");
  e.target.classList.toggle("on", !$("more").classList.contains("hidden"));
});
$("more").addEventListener("submit", (e) => {
  e.preventDefault();
  more = Object.fromEntries(new FormData(e.target));
  loadFiles();
});
$("more-clear").addEventListener("click", () => {
  $("more").reset();
  more = {};
  loadFiles();
});

document.querySelectorAll("#files th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const next = th.dataset.sort;
  order = next === sort ? (order === "asc" ? "desc" : "asc") : (next === "name" || next === "kind" ? "asc" : "desc");
  sort = next;
  loadFiles();
}));

$("files").addEventListener("click", (e) => {
  const tr = e.target.closest("tbody tr");
  if (!tr) return;
  document.querySelectorAll("#files tr.picked").forEach((x) => x.classList.remove("picked"));
  tr.classList.add("picked");
  showDetail(tr.row);
});
$("files").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tbody tr");
  if (tr) openPath(tr.row.path, false);
});
$("files").addEventListener("contextmenu", (e) => {
  const tr = e.target.closest("tbody tr");
  if (tr) showMenu(e, tr.row.path);
});

// ---------- 상세 ----------
async function showDetail(row) {
  picked = row;
  $("detail-empty").classList.toggle("hidden", !!row);
  $("detail-body").classList.toggle("hidden", !row);
  say("detail-message", "");
  if (!row) return;
  $("detail-badge").replaceWith(Object.assign(badge(row.ext, true), {id: "detail-badge"}));
  $("detail-name").textContent = row.name;
  const facts = [["종류", row.kind], ["크기", size(row.size)], ["위치", row.place], ["마지막 수정", fullDate(row.modified)]];
  $("detail-rows").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", {textContent: k}), el("dd", {textContent: v}))));
  const {folders} = await api("/api/chain?path=" + encodeURIComponent(row.path));
  if (picked !== row) return;
  $("detail-path").replaceChildren(...folders.map((f) => {
    const button = el("button", {textContent: f.name, title: f.path});
    button.addEventListener("click", () => openScope({folder: f.path, name: f.name}));
    return el("li", {}, button);
  }), el("li", {}, el("span", {className: "here", textContent: row.name})));
}

async function changed(row, path) {
  // 옮기거나 이름을 바꾼 뒤: 상세는 새 경로로, 목록과 숫자는 다시 읽는다.
  const name = path.slice(path.lastIndexOf("\\") + 1);
  picked = {...row, path, name, ext: extOf(name)};  // 목록을 다시 그릴 때 새 경로의 줄이 골라진 채로 나오게
  await loadState();
  await loadFiles();
  await showDetail(picked);
}

$("act-move").addEventListener("click", (e) => busy(e.target, "detail-message", async () => {
  const row = picked;
  const {path} = await api("/api/move", {path: row.path});
  if (!path) return;
  await changed(row, path);
  say("detail-message", "옮겼습니다.", "ok");
}));

$("act-rename").addEventListener("click", async (e) => {
  const row = picked;
  const name = await ask({title: "이름 바꾸기", text: row.name, value: row.name});
  if (name === null || name === row.name) return;
  busy(e.target, "detail-message", async () => {
    const {path} = await api("/api/rename", {path: row.path, name});
    await changed(row, path);
    say("detail-message", "이름을 바꿨습니다.", "ok");
  });
});

$("act-trash").addEventListener("click", async (e) => {
  const row = picked;
  if (!(await ask({title: "휴지통으로 보낼까요?", text: `${row.name}\n휴지통에서 다시 꺼낼 수 있습니다.`}))) return;
  busy(e.target, "detail-message", async () => {
    await api("/api/trash", {path: row.path});
    await showDetail(null);
    await loadState();
    await loadFiles();
  });
});

// ---------- 대화상자: 입력(value가 있으면)이나 확인. 확인하면 입력값 또는 true, 취소하면 null ----------
function ask({title, text = "", value}) {
  $("dialog-title").textContent = title;
  $("dialog-text").textContent = text;
  const input = $("dialog-input");
  input.classList.toggle("hidden", value === undefined);
  $("dialog").classList.remove("hidden");
  if (value !== undefined) {
    input.value = value;
    input.focus();
    const dot = value.lastIndexOf(".");
    input.setSelectionRange(0, dot > 0 ? dot : value.length);  // 확장자 앞까지 골라 둔다.
  } else {
    $("dialog-ok").focus();
  }
  return new Promise((resolve) => {
    const finish = (answer) => {
      $("dialog").classList.add("hidden");
      $("dialog-form").onsubmit = $("dialog-cancel").onclick = null;
      resolve(answer);
    };
    $("dialog-form").onsubmit = (e) => { e.preventDefault(); finish(value === undefined ? true : input.value); };
    $("dialog-cancel").onclick = () => finish(null);
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  hideMenu();
  if (!$("dialog").classList.contains("hidden")) $("dialog-cancel").click();
});

// ---------- 오른쪽 클릭 메뉴 ----------
function showMenu(e, path) {
  e.preventDefault();
  menuPath = path;
  const menu = $("menu");
  menu.classList.remove("hidden");
  menu.style.left = Math.min(e.clientX, innerWidth - menu.offsetWidth - 4) + "px";
  menu.style.top = Math.min(e.clientY, innerHeight - menu.offsetHeight - 4) + "px";
}

const hideMenu = () => $("menu").classList.add("hidden");
document.addEventListener("click", (e) => { if (!e.target.closest("#menu")) hideMenu(); });
document.addEventListener("scroll", hideMenu, true);

async function openPath(path, reveal) {
  try {
    await api("/api/open", {path, reveal});
  } catch (e) {
    say("detail-message", e.message, "error");
  }
}

$("menu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  hideMenu();
  if (act === "open") openPath(menuPath, false);
  if (act === "reveal") openPath(menuPath, true);
  if (act === "exclude") changeExclude(menuPath, false);
});

async function changeExclude(path, remove) {
  try {
    state.exclude = (await api("/api/exclude", {path, remove})).exclude;
    await loadState();
    if (page === "files") await loadFiles();
  } catch (e) {
    say("detail-message", e.message, "error");
  }
}

// ---------- 다운로드 정리하기 ----------
function choice(label, path, current, onPick) {
  const button = el("button", {textContent: label, title: path, className: lower(path) === lower(current || "") ? "on" : ""});
  button.addEventListener("click", () => onPick(path));
  return button;
}

function showOrganize() {
  const places = [["다운로드", state.known["다운로드"]], ...state.drives.map((d) => [`${d.path} (${d.type})`, d.path])];
  $("source-choices").replaceChildren(...places.map(([label, path]) => choice(label, path, source, setSource)));
  $("source").textContent = source || "고르지 않음";
  $("dest").textContent = dest || "고르지 않음";
  $("undo").disabled = !state.undo;
  $("undo").textContent = state.undo ? `마지막 정리 되돌리기 (${state.undo}개)` : "마지막 정리 되돌리기";
}

function clearPreview() {
  $("run").disabled = true;
  $("preview-card").classList.add("hidden");
}

function setSource(path) {
  source = path;
  clearPreview();
  showOrganize();
}

$("preview").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  clearPreview();
  const {moves} = await api("/api/organize/preview", {source, dest});
  $("preview-table").tBodies[0].replaceChildren(...moves.map((m) => {
    const name = m.from.slice(m.from.lastIndexOf("\\") + 1);
    return el("tr", {},
      el("td", {}, el("div", {className: "file"}, badge(extOf(name)), el("div", {className: "file-name", textContent: name}))),
      el("td", {className: "muted", textContent: m.kind}),
      el("td", {className: "where", textContent: m.to.slice(dest.length).replace(/^\\/, ""), title: m.to}));
  }));
  $("preview-card").classList.toggle("hidden", !moves.length);
  const counts = {};
  moves.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(", ");
  say("organize-message", moves.length ? `${moves.length}개를 옮깁니다 (${summary}). 확인한 뒤 [정리]를 누르세요.` : "옮길 파일이 없습니다.");
  await loadState();
  $("run").disabled = !moves.length;
}));

function report(result, verb) {
  const failed = result.failed.map((f) => `${f.from}: ${f.error}`).join("\n");
  say("organize-message", `${result.moved}개를 ${verb}.` + (result.failed.length ? ` ${result.failed.length}개는 못 했습니다.\n${failed}` : ""),
      result.failed.length ? "error" : "ok");
}

$("run").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  const result = await api("/api/organize/run", {source, dest});
  clearPreview();
  report(result, "옮겼습니다");
  await loadState();
}).then(() => { e.target.disabled = true; }));  // 옮긴 뒤에는 다시 미리보기를 해야 정리할 수 있다.

$("undo").addEventListener("click", async (e) => {
  if (!(await ask({title: "마지막 정리를 되돌릴까요?", text: `${state.undo}개를 원래 자리로 옮깁니다.`}))) return;
  busy(e.target, "organize-message", async () => {
    const result = await api("/api/organize/undo", {});
    clearPreview();
    report(result, "되돌렸습니다");
    await loadState();
  });
});

document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  const {path} = await api("/api/pick", {});
  if (!path) return;
  if (button.dataset.pick === "source") setSource(path);
  else if (button.dataset.pick === "dest") { dest = path; clearPreview(); showOrganize(); }
  else await saveSettings({roots: [...state.settings.roots, path]}, "index-progress");
}));

// ---------- 설정 ----------
async function saveSettings(changes, messageId) {
  try {
    state.settings = (await api("/api/settings", changes)).settings;
    showSetup();
    return true;
  } catch (e) {
    $(messageId).textContent = e.message;
    return false;
  }
}

function removable(text, onRemove) {
  const button = el("button", {className: "ghost small", textContent: "빼기"});
  button.addEventListener("click", onRemove);
  return el("li", {}, el("span", {className: "path", textContent: text}), button);
}

function showSetup() {
  $("views").replaceChildren(...Object.keys(VIEWS).map((view) => {
    const button = el("button", {},
      el("span", {className: "label"}, VIEWS[view].mark && state.counts[view] ? el("span", {className: "mark"}) : null, viewTitle(view)),
      el("span", {className: "count"}, state.counts[view].toLocaleString(), el("small", {textContent: "개"})));
    button.addEventListener("click", () => openScope({all: true}, view));
    return button;
  }));
  $("access-off").classList.toggle("hidden", state.last_access);
  const roots = state.settings.roots;
  $("roots").replaceChildren(...roots.map((root) => removable(root, () => saveSettings({roots: roots.filter((r) => r !== root)}, "index-progress"))));
  const has = new Set(roots.map(lower));
  const places = [...Object.entries(state.known), ...state.drives.map((d) => [d.path, d.path])].filter(([, path]) => !has.has(lower(path)));
  $("root-choices").replaceChildren(...places.map(([label, path]) => {
    const button = el("button", {textContent: "+ " + label, title: path});
    button.addEventListener("click", () => saveSettings({roots: [...roots, path]}, "index-progress"));
    return button;
  }));
  $("recent_days").value = state.settings.recent_days;
  $("stale_days").value = state.settings.stale_days;
  $("excludes").replaceChildren(...state.exclude.map((rule) =>
    removable(`${rule.kind === "folder" ? "폴더" : "파일"}  ${rule.path}`, () => changeExclude(rule.path, true))));
  $("excludes-empty").classList.toggle("hidden", state.exclude.length > 0);
}

$("days-save").addEventListener("click", async () => {
  const changes = {recent_days: Number($("recent_days").value), stale_days: Number($("stale_days").value)};
  if (await saveSettings(changes, "days-message")) {
    say("days-message", "저장했습니다.", "ok");
    await loadState();
  } else {
    $("days-message").className = "message error";
  }
});

$("reindex").addEventListener("click", async () => {
  try {
    showIndex(await api("/api/index", {}));
    pollIndex();
  } catch (e) {
    $("index-progress").textContent = e.message;
  }
});

// ---------- 시작 ----------
(async () => {
  await loadState();
  await loadFiles();
  if (state.index.running) pollIndex();
})();
