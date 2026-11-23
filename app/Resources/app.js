"use strict";

const $ = (id) => document.getElementById(id);
const storageKey = "pdf-room-job";
const maxBytes = 50 * 1024 * 1024;
const dropzone = $("dropzone");
const fileInput = $("file");
let file = null;
let currentJob = null;
let polling = null;
let submitting = false;
let provider = {mode: "codex", base_url: "", model: ""};
let viewName = "account";
let accountPoll = null;

function info(message = "") {
  $("error").textContent = message;
  $("error").hidden = !message;
}

function show(view) {
  viewName = view;
  $("account").hidden = view !== "account";
  $("provider-panel").hidden = view !== "settings";
  $("settings").disabled = view === "working";
  dropzone.hidden = view !== "upload";
  $("selection").hidden = view !== "selection";
  $("job-panel").hidden = view !== "working";
  $("results").hidden = view !== "done";
}

function select(files) {
  if (submitting || currentJob) return;
  if (files.length !== 1 || !/\.pdf$/i.test(files[0].name)) {
    info("PDFを1つ選んでください。");
    return;
  }
  if (files[0].size > maxBytes) {
    info("PDFは50 MB以内にしてください。");
    return;
  }
  file = files[0];
  $("filename").textContent = file.name;
  $("filesize").textContent = file.size >= 1024 * 1024
    ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(file.size / 1024))} KB`;
  info();
  show("selection");
  $("start").focus();
}

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.detail || "操作を完了できませんでした。");
    error.status = response.status;
    throw error;
  }
  return body;
}

function updateProgress(job) {
  $("stage").textContent = job.stage;
  $("percent").textContent = job.progress ? `${job.progress}%` : "";
  if (job.progress) $("progress").value = job.progress;
  else $("progress").removeAttribute("value");
  $("count").textContent = job.count;
  $("cancel").disabled = job.state === "cancelling";
}

function forget() {
  clearTimeout(polling);
  polling = null;
  currentJob = null;
  sessionStorage.removeItem(storageKey);
}

function render(job) {
  if (job.state === "done") {
    for (const format of ["mono", "dual"]) {
      $(format).href = `/api/jobs/${job.id}/${format}`;
    }
    info();
    show("done");
    return;
  }
  if (job.state === "failed" || job.state === "cancelled") {
    forget();
    show(file ? "selection" : "upload");
    info(job.error || job.stage);
    return;
  }

  show("working");
  updateProgress(job);
  polling = setTimeout(checkStatus, 850);
}

async function checkStatus() {
  const id = currentJob;
  if (!id) return;
  try {
    const job = await request(`/api/jobs/${id}`);
    if (currentJob !== id) return;
    info();
    render(job);
  } catch (error) {
    if (currentJob !== id) return;
    if (error.status === 404) {
      forget();
      show(file ? "selection" : "upload");
      info(error.message);
    } else {
      info("接続が一時的に途切れました。進み具合を再確認しています。");
      polling = setTimeout(checkStatus, 2500);
    }
  }
}

$("choose").addEventListener("click", () => fileInput.click());
$("replace").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) select(fileInput.files);
  fileInput.value = "";
});
dropzone.addEventListener("dragover", (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  dropzone.classList.add("drag");
});
dropzone.addEventListener("dragleave", (event) => {
  if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("drag");
});
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("drag");
  select(event.dataTransfer.files);
});
window.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes("Files")) event.preventDefault();
});
window.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) event.preventDefault();
});

$("start").addEventListener("click", async () => {
  if (!file || submitting || currentJob) return;
  submitting = true;
  $("start").disabled = true;
  $("settings").disabled = true;
  info();
  try {
    const job = await request("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/pdf", "X-PDF-Name": encodeURIComponent(file.name) },
      body: file,
    });
    currentJob = job.id;
    sessionStorage.setItem(storageKey, currentJob);
    render(job);
    $("cancel").focus();
  } catch (error) {
    if (error.status === 401) await routeToInput();
    else info(error.status ? error.message : "PDFを読み込めませんでした。再試行してください。");
  } finally {
    submitting = false;
    $("start").disabled = false;
    $("settings").disabled = viewName === "working";
  }
});

$("cancel").addEventListener("click", async () => {
  if (!currentJob) return;
  $("cancel").disabled = true;
  try {
    await request(`/api/jobs/${currentJob}`, { method: "DELETE" });
  } catch (error) {
    info(error.status ? error.message : "中止できませんでした。再試行してください。");
    $("cancel").disabled = false;
  }
});
$("again").addEventListener("click", () => {
  forget();
  file = null;
  info();
  show("upload");
  $("choose").focus();
});

let waitingForLogin = false;

function showProvider(mode) {
  $("mode-codex").setAttribute("aria-pressed", mode === "codex");
  $("mode-api").setAttribute("aria-pressed", mode === "api");
  $("codex-config").hidden = mode !== "codex";
  $("api-config").hidden = mode !== "api";
}

function openSettings(mode = provider.mode) {
  if (currentJob && viewName !== "done") return;
  clearTimeout(accountPoll);
  $("base-url").value = provider.base_url || "https://api.openai.com/v1";
  $("model").value = provider.model || "";
  $("api-key").value = "";
  info();
  showProvider(mode);
  show("settings");
  $("mode-" + mode).focus();
}

function updateProviderLabel() {
  const usingApi = provider.mode === "api";
  $("engine-label").textContent = usingApi ? "互換API" : "Codex";
  $("privacy").textContent = usingApi
    ? "翻訳する本文は設定したAPIに送られます。料金や上限は接続先によります。"
    : "翻訳する本文はOpenAIに送られ、ログイン済みのCodex利用枠を使います。";
}

async function routeToInput() {
  updateProviderLabel();
  if (currentJob) {
    await checkStatus();
    return;
  }
  if (provider.mode === "api") {
    show(file ? "selection" : "upload");
    return;
  }
  show("account");
  await checkAccount();
}

async function checkAccount() {
  const button = $("signin");
  const install = $("install-codex");
  const message = $("account-status");
  clearTimeout(accountPoll);
  if (viewName !== "account") return;
  try {
    const account = await request("/api/account");
    if (viewName !== "account") return;
    if (account.state === "ready") {
      info();
      show(file ? "selection" : "upload");
      return;
    }
    button.hidden = account.state !== "signed-out";
    install.hidden = !["missing", "install-failed"].includes(account.state);
    button.disabled = false;
    install.disabled = false;
    if (account.state === "signing-in") {
      message.textContent = "ブラウザでログインを完了してください。";
      accountPoll = setTimeout(checkAccount, 1500);
    } else if (account.state === "installing") {
      message.textContent = "公式Codex CLIをインストールしています。";
      accountPoll = setTimeout(checkAccount, 1800);
    } else if (account.state === "signed-out") {
      message.textContent = waitingForLogin
        ? "ログインを確認できませんでした。もう一度お試しください。"
        : "ご自身のChatGPTアカウントでログインすると、Codexの利用枠で翻訳できます。";
      waitingForLogin = false;
    } else if (account.state === "missing") {
      message.textContent = "Codexを使うには、最初に公式CLIをインストールします。";
    } else if (account.state === "install-failed") {
      message.textContent = "インストールに失敗しました。ネット接続を確認して再試行してください。";
    } else {
      message.textContent = "Codexを確認できません。PDF翻訳室 Mark.Ⅱを開き直してください。";
    }
  } catch {
    message.textContent = "接続を確認しています。";
    accountPoll = setTimeout(checkAccount, 2500);
  }
}

$("settings").addEventListener("click", () => openSettings());
$("api-alternative").addEventListener("click", () => openSettings("api"));
$("settings-close").addEventListener("click", () => {
  $("api-key").value = "";
  info();
  routeToInput();
});
$("mode-codex").addEventListener("click", () => {
  $("api-key").value = "";
  showProvider("codex");
});
$("mode-api").addEventListener("click", () => showProvider("api"));

async function saveProvider(settings, button) {
  button.disabled = true;
  info();
  try {
    provider = await request("/api/provider", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(settings),
    });
    $("api-key").value = "";
    await routeToInput();
  } catch (error) {
    info(error.status ? error.message : "翻訳設定を保存できませんでした。");
  } finally {
    button.disabled = false;
  }
}
$("use-codex").addEventListener("click", () => saveProvider({mode: "codex"}, $("use-codex")));
$("api-config").addEventListener("submit", (event) => {
  event.preventDefault();
  saveProvider({
    mode: "api", base_url: $("base-url").value, model: $("model").value, api_key: $("api-key").value,
  }, $("use-api"));
});
$("install-codex").addEventListener("click", async () => {
  $("install-codex").disabled = true;
  info();
  try {
    await request("/api/account/install", {method: "POST"});
    await checkAccount();
  } catch (error) {
    info(error.status ? error.message : "Codexをインストールできませんでした。");
    $("install-codex").disabled = false;
  }
});
$("signin").addEventListener("click", async () => {
  const button = $("signin");
  button.disabled = true;
  waitingForLogin = true;
  try {
    await request("/api/account/login", {method: "POST"});
    await checkAccount();
  } catch (error) {
    waitingForLogin = false;
    info(error.status ? error.message : "ログイン画面を開けませんでした。");
    button.disabled = false;
  }
});

async function startApp() {
  try {
    provider = await request("/api/provider");
    currentJob = sessionStorage.getItem(storageKey);
    if (currentJob) {
      show("working");
      $("stage").textContent = "翻訳状況を確認しています";
      $("cancel").disabled = true;
    }
    await routeToInput();
  } catch {
    info("翻訳画面に接続できません。アプリを開き直してください。");
  }
}
startApp();
