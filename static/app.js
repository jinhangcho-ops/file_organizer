"use strict";
// 화면: 정리 / 검색 / 모아 보기 / 설정 탭과 왼쪽의 나무. 서버는 server.py.

const $ = (id) => document.getElementById(id);
let state = null;             // /api/state의 답(설정, 디스크, 알려진 폴더, 종류, 색인 상태, 예외)
let source = "", dest = "";   // 정리할 곳, 놓을 곳(미리보기를 누르면 서버가 검사하고 기억한다)
let recentMode = "downloaded";
let menuPath = null;          // 오른쪽 클릭한 파일·폴더의 경로
const nodes = new Map();      // 나무의 항목: 경로(소문자) → li
const kinds = new Set();      // 검색에서 고른 종류

async function api(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({error: `응답을 읽지 못했습니다 (${response.status})`}));
  if (!response.ok) throw new Error(body.error || String(response.status));
  return body;
}

// 요청하는 동안 버튼을 잠그고, 실패하면 문구를 보여 준다.
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
  const box = $(id);
  box.textContent = text;
  box.className = "message " + kind;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

const lower = (path) => path.toLowerCase();
const parentOf = (path) => path.slice(0, path.lastIndexOf("\\")) || path;

function size(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes || 0, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + units[i];
}

function when(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000), two = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

// ---------- 탭 ----------
document.querySelector(".tabs").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-tab]");
  if (!button) return;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b === button));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("hidden", tab.id !== "tab-" + button.dataset.tab));
});

// ---------- 상태 ----------
async function loadState() {
  state = await api("/api/state");
  source = source || state.settings.source;
  dest = dest || state.settings.dest;
  $("access-off").classList.toggle("hidden", state.last_access);
  showOrganize();
  showKinds();
  showDays();
  showRoots();
  showExcludes();
  showIndex(state.index);
}

function showIndex(status) {
  const count = `색인한 파일 ${state.indexed.toLocaleString()}개`;
  if (status.running) {
    $("index-status").textContent = `색인하는 중… ${status.done.toLocaleString()}개 확인`;
    $("index-progress").textContent = `${status.done.toLocaleString()}개 확인 ${status.current}`;
  } else {
    $("index-status").textContent = count + (status.finished ? ` · 마지막 갱신 ${when(status.finished)}` : "");
    $("index-progress").textContent = status.error ? "실패: " + status.error : "";
  }
  $("reindex").disabled = status.running;
}

async function pollIndex() {
  const status = await api("/api/index");
  state.index = status;
  showIndex(status);
  if (status.running) {
    setTimeout(pollIndex, 1000);
  } else {
    await loadState();
    await loadTree();
  }
}

// ---------- 정리 ----------
function choice(label, path, current, onPick) {
  const button = el("button", {textContent: label, title: path});
  button.classList.toggle("on", lower(path) === lower(current || ""));
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
  $("preview-table").classList.add("hidden");
  $("preview-summary").replaceChildren();
}

function setSource(path) {
  source = path;
  clearPreview();
  showOrganize();
}

$("preview").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  clearPreview();
  const {moves} = await api("/api/organize/preview", {source, dest});
  const counts = {};
  moves.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  $("preview-summary").replaceChildren(...Object.entries(counts).map(([kind, n]) => el("span", {textContent: `${kind} ${n}개`})));
  $("preview-table").tBodies[0].replaceChildren(...moves.map((m) => el("tr", {},
    el("td", {textContent: m.from.slice(m.from.lastIndexOf("\\") + 1)}),
    el("td", {className: "kind", textContent: m.kind}),
    el("td", {className: "where", textContent: m.to}))));
  $("preview-table").classList.toggle("hidden", !moves.length);
  say("organize-message", moves.length ? `${moves.length}개를 옮깁니다. 확인한 뒤 [정리]를 누르세요.` : "옮길 파일이 없습니다.");
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
  await loadTree();
}).then(() => { e.target.disabled = true; }));  // 옮긴 뒤에는 다시 미리보기를 해야 정리할 수 있다.

$("undo").addEventListener("click", (e) => {
  if (!confirm(`마지막 정리(${state.undo}개)를 원래 자리로 되돌릴까요?`)) return;
  busy(e.target, "organize-message", async () => {
    const result = await api("/api/organize/undo", {});
    clearPreview();
    report(result, "되돌렸습니다");
    await loadState();
    await loadTree();
  });
});

document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  const {path} = await api("/api/pick", {});
  if (!path) return;
  if (button.dataset.pick === "source") setSource(path);
  else if (button.dataset.pick === "dest") { dest = path; clearPreview(); showOrganize(); }
  else await saveRoots([...state.settings.roots, path]);
}));

// ---------- 검색·모아 보기의 파일 표 ----------
function showFiles(table, rows, more, dateLabel, dateOf) {
  table.tHead.replaceChildren(el("tr", {}, ...["이름", "위치", "종류", "크기", dateLabel].map((t) => el("th", {textContent: t}))));
  table.tBodies[0].replaceChildren(...rows.map((row) => {
    const tr = el("tr", {},
      el("td", {}, el("div", {textContent: row.name}), row.snippet ? el("div", {className: "snippet", textContent: "… " + row.snippet.replace(/\s+/g, " ") + " …"}) : null),
      el("td", {className: "where", textContent: parentOf(row.path)}),
      el("td", {className: "kind", textContent: row.kind}),
      el("td", {className: "num", textContent: size(row.size)}),
      el("td", {className: "when", textContent: when(dateOf(row))}));
    tr.dataset.path = row.path;
    return tr;
  }));
  table.classList.toggle("hidden", !rows.length);
  return rows.length ? `${rows.length.toLocaleString()}개` + (more ? " (더 있습니다. 조건을 좁히세요)" : "") : "찾은 파일이 없습니다.";
}

function pickRow(tr) {
  document.querySelectorAll("tr.picked, .row.picked").forEach((x) => x.classList.remove("picked"));
  tr.classList.add("picked");
}

document.querySelectorAll("table.files").forEach((table) => {
  table.addEventListener("click", (e) => {
    const tr = e.target.closest("tbody tr");
    if (!tr) return;
    pickRow(tr);
    revealInTree(tr.dataset.path);
  });
  table.addEventListener("dblclick", (e) => {
    const tr = e.target.closest("tbody tr");
    if (tr) openPath(tr.dataset.path, false);
  });
  table.addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tbody tr");
    if (tr) showMenu(e, tr.dataset.path);
  });
});

// ---------- 검색 ----------
function showKinds() {
  $("kind-choices").replaceChildren(...state.kinds.map((kind) => {
    const button = el("button", {type: "button", textContent: kind});
    button.classList.toggle("on", kinds.has(kind));
    button.addEventListener("click", () => {
      kinds.has(kind) ? kinds.delete(kind) : kinds.add(kind);
      button.classList.toggle("on", kinds.has(kind));
    });
    return button;
  }));
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const query = Object.fromEntries(new FormData(e.target));
  query.kinds = [...kinds];
  busy(e.target.querySelector("button.primary"), "search-message", async () => {
    const found = await api("/api/search", query);
    say("search-message", showFiles($("search-table"), found.rows, found.more, "고친 날짜", (r) => r.modified));
  });
});

// ---------- 모아 보기 ----------
const MODES = {
  downloaded: {setting: "recent_days", before: "최근", after: "일 안에 받은 파일", label: "받은 날짜", date: (r) => r.created},
  modified: {setting: "recent_days", before: "최근", after: "일 안에 고친 파일", label: "고친 날짜", date: (r) => r.modified},
  stale: {setting: "stale_days", before: "", after: "일 넘게 안 본 파일", label: "마지막으로 본·고친 날짜", date: (r) => Math.max(r.accessed, r.modified)},
};

function showDays() {
  const mode = MODES[recentMode];
  $("days").value = state.settings[mode.setting];
  $("days-label").textContent = mode.before;
  $("days-unit").textContent = mode.after;
  document.querySelectorAll(".segment button").forEach((b) => b.classList.toggle("on", b.dataset.mode === recentMode));
}

document.querySelector(".segment").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-mode]");
  if (!button) return;
  recentMode = button.dataset.mode;
  showDays();
  runRecent();
});

function runRecent() {
  return busy($("recent-run"), "recent-message", async () => {
    const days = Number($("days").value);
    const found = await api("/api/recent", {mode: recentMode, days: Number.isInteger(days) ? days : $("days").value});
    state.settings[MODES[recentMode].setting] = days;
    const mode = MODES[recentMode];
    say("recent-message", showFiles($("recent-table"), found.rows, found.more, mode.label, mode.date));
  });
}

$("recent-run").addEventListener("click", runRecent);

// ---------- 설정: 색인할 폴더, 예외 ----------
async function saveRoots(roots) {
  try {
    state.settings = (await api("/api/settings", {roots})).settings;
    showRoots();
  } catch (e) {
    $("index-progress").textContent = e.message;
  }
}

function removable(text, onRemove) {
  const button = el("button", {className: "small", textContent: "빼기"});
  button.addEventListener("click", onRemove);
  return el("li", {}, el("span", {className: "path", textContent: text}), button);
}

function showRoots() {
  const roots = state.settings.roots;
  $("roots").replaceChildren(...roots.map((root) => removable(root, () => saveRoots(roots.filter((r) => r !== root)))));
  const has = new Set(roots.map(lower));
  const places = [...Object.entries(state.known), ...state.drives.map((d) => [d.path, d.path])].filter(([, path]) => !has.has(lower(path)));
  $("root-choices").replaceChildren(...places.map(([label, path]) => {
    const button = el("button", {textContent: "+ " + label, title: path});
    button.addEventListener("click", () => saveRoots([...roots, path]));
    return button;
  }));
}

function showExcludes() {
  $("excludes").replaceChildren(...state.exclude.map((rule) =>
    removable(`${rule.kind === "folder" ? "폴더" : "파일"}  ${rule.path}`, () => changeExclude(rule.path, true))));
  $("excludes-empty").classList.toggle("hidden", state.exclude.length > 0);
}

async function changeExclude(path, remove) {
  try {
    state.exclude = (await api("/api/exclude", {path, remove})).exclude;
    showExcludes();
    if (!$("recent-table").classList.contains("hidden")) await runRecent();
  } catch (e) {
    say("recent-message", e.message, "error");
  }
}

$("reindex").addEventListener("click", async () => {
  try {
    showIndex(await api("/api/index", {}));
    pollIndex();
  } catch (e) {
    $("index-progress").textContent = e.message;
  }
});

// ---------- 나무 ----------
function treeNode(item, folder) {
  const twist = el("span", {className: "twist", textContent: folder ? "▸" : ""});
  const meta = folder ? `${item.files.toLocaleString()}개 · ${size(item.size)}` : size(item.size);
  const row = el("div", {className: "row", title: item.path}, twist, el("span", {className: "name", textContent: item.name}), el("span", {className: "meta", textContent: meta}));
  const li = el("li", {className: folder ? "folder" : "file"}, row);
  li.dataset.path = item.path;
  if (folder) li.append(el("ul", {className: "hidden"}));
  nodes.set(lower(item.path), li);
  return li;
}

function fill(list, data) {
  list.replaceChildren(...data.folders.map((f) => treeNode(f, true)), ...data.files.map((f) => treeNode(f, false)));
  if (data.more) list.append(el("li", {className: "more", textContent: `… 파일 ${data.more.toLocaleString()}개 더 (검색으로 찾으세요)`}));
}

async function loadTree() {
  nodes.clear();
  const data = await api("/api/tree");
  fill($("tree"), data);
  $("tree-empty").classList.toggle("hidden", data.folders.length > 0);
}

async function expand(li, open = true) {
  const list = li.querySelector(":scope > ul");
  if (!li.dataset.loaded) {
    fill(list, await api("/api/tree?path=" + encodeURIComponent(li.dataset.path)));
    li.dataset.loaded = "1";
  }
  list.classList.toggle("hidden", !open);
  li.querySelector(":scope > .row .twist").textContent = open ? "▾" : "▸";
}

async function revealInTree(path) {
  const {folders} = await api("/api/chain?path=" + encodeURIComponent(path));
  for (const folder of folders) {
    const li = nodes.get(lower(folder));
    if (!li) return;
    await expand(li);
  }
  const target = nodes.get(lower(path));
  if (!target) return;
  const row = target.querySelector(":scope > .row");
  document.querySelectorAll(".row.picked").forEach((x) => x.classList.remove("picked"));
  row.classList.add("picked");
  row.scrollIntoView({block: "center"});
}

$("tree").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  const li = row.parentElement;
  document.querySelectorAll(".row.picked").forEach((x) => x.classList.remove("picked"));
  row.classList.add("picked");
  if (li.classList.contains("folder")) expand(li, li.querySelector(":scope > ul").classList.contains("hidden"));
});
$("tree").addEventListener("dblclick", (e) => {
  const li = e.target.closest("li.file");
  if (li) openPath(li.dataset.path, false);
});
$("tree").addEventListener("contextmenu", (e) => {
  const li = e.target.closest("li.folder, li.file");
  if (li) showMenu(e, li.dataset.path);
});
$("tree-reload").addEventListener("click", loadTree);

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
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });
document.addEventListener("scroll", hideMenu, true);

async function openPath(path, reveal) {
  try {
    await api("/api/open", {path, reveal});
  } catch (e) {
    alert(e.message);
  }
}

$("menu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  hideMenu();
  if (act === "open") openPath(menuPath, false);
  if (act === "reveal") openPath(menuPath, true);
  if (act === "tree") revealInTree(menuPath);
  if (act === "exclude") changeExclude(menuPath, false);
});

// ---------- 시작 ----------
(async () => {
  await loadState();
  await loadTree();
  if (state.index.running) pollIndex();
})();
