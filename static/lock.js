"use strict";
// 잠금 창: 비밀번호 정하기, 금고 위치, 잠금 목록(열기·다시 잠그기·꺼내기·잠금 풀기), 본 창에서 고른 폴더 잠그기·금고로 모으기.
// 이 창은 desktop.open_lock이 화면 캡처에서 빼 둔다. 바꾼 뒤에는 본 창이 목록을 다시 읽게 한다(refresh).

let lockState = null;  // /api/lock/state의 답
let view = "manage";  // 잠금 관리(manage) / 잠금 파일 보기(files). 잠금 폴더가 있을 때만 바꿀 수 있다.
let filesAt = "";  // 잠금 파일 보기에서 보고 있는 폴더(""는 잠금 폴더들)
const password = (title, text = "") => ask({title, text, value: "", secret: true});
const messageId = () => (view === "files" ? "files-message" : "lock-message");

const applyTheme = followTheme(() => lockState?.theme);

async function load() {
  lockState = await api("/api/lock/state");
  applyTheme();
  if (!lockState.locks.length) view = "manage";
  $("lock-tabs").classList.toggle("hidden", !lockState.locks.length);
  document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("p", b.dataset.view === view));
  $("manage-box").classList.toggle("hidden", view !== "manage");
  $("files-box").classList.toggle("hidden", view !== "files");
  if (view === "files") await showFiles(filesAt);
  $("target-box").classList.toggle("hidden", !lockState.target || view !== "manage");
  $("target").textContent = lockState.target;
  $("locks").replaceChildren(...lockState.locks.map(lockRow));
  $("locks-empty").classList.toggle("hidden", lockState.locks.length > 0);
  const vault = lockState.vault;
  $("vault-path").textContent = vault.path ? `금고: ${vault.path} (${vault.count.toLocaleString()}개${vault.open ? ", 열림" : ""})` : "금고: 정하지 않음";
  $("lock-vault").textContent = vault.path ? "금고 위치 바꾸기" : "금고 위치 정하기";
  $("lock-password").textContent = lockState.password ? "비밀번호 바꾸기" : "비밀번호 정하기";
}

// 잠금을 바꾸는 요청. 성공하면 다시 그리고 본 창의 목록·숫자도 다시 읽게 한다.
// 바꾸기는 됐는데 다시 읽기만 실패하면, 성공을 알리고 본 창도 새로 읽게 한 뒤 다시 읽기 실패를 따로 알린다.
async function lockCall(url, data, done) {
  let answer;
  try {
    answer = await api(url, data);
  } catch (e) {
    say(messageId(), e.message, "error");
    return null;
  }
  if (data.path) await api("/api/window", {action: "refresh"}).catch(() => {});  // 비밀번호·금고 위치는 본 창의 목록을 바꾸지 않는다
  const text = !done ? "" : typeof done === "function" ? done(answer) : done;
  if (text) say(messageId(), text, "ok");
  await load().catch((e) => say(messageId(), `${text} 목록을 다시 읽지 못했어요: ${e.message}`.trim(), "error"));
  return answer;
}

document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
  view = b.dataset.view;
  load().catch((e) => say(messageId(), e.message, "error"));
}));
$("lock-check").addEventListener("click", () => load().then(() => say("lock-message", "다시 확인했어요.", "ok")).catch((e) => say("lock-message", e.message, "error")));

// 잠금 파일 보기: path가 비면 잠금 폴더들, 아니면 열어 둔 잠금 폴더 안. 다시 잠가 볼 수 없게 됐으면 처음으로 돌아간다.
async function showFiles(path) {
  let found;
  try {
    found = await api(`/api/lock/files?path=${encodeURIComponent(path)}`);
  } catch (e) {
    if (path) return showFiles("");
    return say("files-message", e.message, "error");
  }
  filesAt = found.path;
  $("files-path").textContent = found.label || "잠금 폴더";
  $("files-up").disabled = !found.path;
  $("files-up").onclick = () => showFiles(found.parent);
  const rows = found.path ? [...found.folders.map(folderRow), ...found.files.map(fileRow)] : found.folders.map(topRow);
  $("files-list").replaceChildren(...rows);
  $("files-empty").classList.toggle("hidden", rows.length > 0);
}

function pickerRow(text, small, file) {
  const row = el("button", {type: "button", className: "picker-row" + (file ? " file" : "")});
  if (!file) row.insertAdjacentHTML("beforeend", FOLDER_ICON);
  row.append(el("span", {textContent: text, title: text}));
  if (small) row.append(el("small", {textContent: small}));
  return row;
}

function topRow(item) {
  const row = pickerRow(item.label, item.open ? "열림" : "잠김 · 눌러서 열기");
  row.addEventListener("click", async () => {
    if (item.open) return showFiles(item.path);
    const pw = await password("잠금 폴더 열기", `${item.label}\n\n열면 이 창에서 안의 파일을 볼 수 있어요. 다 보면 잠금 관리에서 [다시 잠그기]를 누르세요(프로그램을 닫을 때도 다시 잠가요).`);
    if (pw === null) return;
    const answer = await lockCall("/api/lock/open", {path: item.label, password: pw, explorer: false});
    if (answer) await showFiles(answer.path);
  });
  return row;
}

function folderRow(item) {
  const row = pickerRow(item.name, "");
  row.addEventListener("click", () => showFiles(item.path));
  return row;
}

function fileRow(item) {
  const row = pickerRow(item.name, `${size(item.size)} · ${new Date(item.modified * 1000).toLocaleDateString()}`, true);
  row.title = "두 번 누르면 열어요";
  row.addEventListener("dblclick", () => api("/api/lock/open_file", {path: item.path})
    .then(({note}) => say("files-message", note, "ok")).catch((e) => say("files-message", e.message, "error")));
  return row;
}

async function lockFolder(vault) {
  const path = lockState.target;
  if (!lockState.password) return say("lock-message", "먼저 [비밀번호 정하기]를 하세요.", "error");
  if (vault && !lockState.vault.path) return say("lock-message", "먼저 [금고 위치 정하기]를 하세요.", "error");
  const pw = vault
    ? await password("금고로 모을까요?", `${path}\n\n금고(${lockState.vault.path}) 안으로 옮기고 금고를 잠가요. 비밀번호를 넣어야 열리고, [꺼내기]로 이 자리에 되돌려요.`)
    : await password("폴더를 잠글까요?", `${path}\n\n비밀번호를 넣어야 열려요. 잠그면 목록과 검색에서 빠져요.`);
  if (pw !== null && await lockCall("/api/lock", {path, password: pw, vault}, vault ? "금고로 모았어요." : "잠갔어요.")) {
    $("target-box").classList.add("hidden");
  }
}

$("do-lock").addEventListener("click", () => lockFolder(false));
$("do-vault").addEventListener("click", () => lockFolder(true));

$("lock-vault").addEventListener("click", async () => {
  if (!lockState.password) return say("lock-message", "먼저 [비밀번호 정하기]를 하세요.", "error");
  const pw = await password("금고 위치 정하기", "다음 창에서 금고를 둘 곳을 고르세요. 그 안에 숨긴 .vault 폴더를 만들어 잠가요. 금고와 같은 디스크의 폴더만 모을 수 있어요.");
  if (pw === null) return;
  const place = await pickPath({title: "금고를 둘 곳"});
  if (place) await lockCall("/api/lock/vault", {password: pw, place}, (answer) => "금고를 정했어요: " + answer.vault);
});

async function takeOut(item) {
  const pw = await password("금고에서 꺼낼까요?", `${item.path}\n\n원래 자리로 되돌려요. 같은 이름이 있으면 ‘이름 (2)’로 둬요.`);
  if (pw === null) return;
  let answer = await lockCall("/api/lock/take_out", {path: item.path, password: pw});
  if (answer?.need_folder) {
    if (!(await ask({title: "꺼낼 곳을 골라 주세요", text: answer.need_folder}))) return;
    const folder = await pickPath({title: "꺼낼 곳"});
    if (!folder) return;
    answer = await lockCall("/api/lock/take_out", {path: item.path, password: pw, folder});
  }
  if (answer?.path) say("lock-message", `꺼냈어요: ${answer.path}. 다음 [색인 갱신]부터 목록에 나와요.`, "ok");
}

$("lock-password").addEventListener("click", async () => {
  let old;
  if (lockState.password) {
    old = await password("지금 비밀번호");
    if (old === null) return;
  }
  const next = await password("새 비밀번호", "8자 이상. 잊으면 README의 ‘비밀번호를 잊었을 때’ 명령으로 풀어요.");
  if (next === null) return;
  if (await password("새 비밀번호 확인") !== next) return say("lock-message", "두 번 넣은 비밀번호가 달라요.", "error");
  await lockCall("/api/lock/password", {old, new: next}, "비밀번호를 정했어요.");
});

function lockRow(item) {
  const row = el("div");
  row.insertAdjacentHTML("beforeend", FOLDER_ICON);
  const where = item.vault ? "금고" : "제자리";
  // sealed: 서버가 실제 Windows 권한·속성으로 확인한 결과(잠겨 있어야 할 때만 true·false)
  const status = item.missing ? "찾을 수 없음" : item.open ? "열림" : item.sealed ? "잠김(확인함)" : "잠금이 풀려 있음";
  row.append(el("span", {className: "mono", textContent: `${where}·${status}  ${item.path}`,
    title: item.missing ? "앱 밖에서 옮기거나 지운 것 같아요. [잠금 풀기]로 목록에서 뺄 수 있어요."
      : item.sealed === false ? "기록은 잠김인데 실제로는 열리거나 숨겨져 있지 않아요. [다시 잠그기]를 누르세요." : item.path}));
  if (item.sealed === false) row.classList.add("unsealed");
  const button = (text, act) => {
    const b = el("button", {className: "btn s", textContent: text});
    b.addEventListener("click", act);
    row.append(b);
  };
  if (item.open || item.sealed === false) button("다시 잠그기", () => lockCall("/api/lock/close", {path: item.path}, item.vault ? "금고를 다시 잠갔어요." : "다시 잠갔어요."));
  else if (!item.missing) button("열기", async () => {
    const pw = await password(item.vault ? "금고에서 열기" : "잠금 폴더 열기", item.vault ? `${item.path}\n\n금고를 잠시 풀고 열어요.` : item.path);
    if (pw !== null) await lockCall("/api/lock/open", {path: item.path, password: pw});
  });
  if (item.vault) button("꺼내기", () => takeOut(item));
  else button("잠금 풀기", async () => {
    const pw = await password("잠금을 아주 풀까요?", `${item.path}\n\n다음 [색인 갱신]부터 목록에 다시 나와요.`);
    if (pw !== null) await lockCall("/api/lock/release", {path: item.path, password: pw}, "잠금을 풀었어요.");
  });
  return row;
}

load();
