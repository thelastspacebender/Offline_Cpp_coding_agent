const vscode = require("vscode");
const http = require("http");
const path = require("path");

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = 18080;

function requestBackend(method, urlPath, bodyObj, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const payload = bodyObj ? JSON.stringify(bodyObj) : null;
    const options = {
      hostname: BACKEND_HOST,
      port: BACKEND_PORT,
      path: urlPath,
      method,
      headers: payload
        ? {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(payload, "utf8")
          }
        : {}
    };

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on("data", (d) => chunks.push(d));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        try {
          resolve(JSON.parse(text));
        } catch {
          reject(new Error(`Invalid JSON from backend: ${text.slice(0, 200)}`));
        }
      });
    });

    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("Request timed out"));
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function openFileAtLine(filePathOrName, lineNumber) {
  let targetUri = null;
  if (filePathOrName && (filePathOrName.includes(":") || filePathOrName.startsWith("/") || filePathOrName.startsWith("\\\\"))) {
    targetUri = vscode.Uri.file(filePathOrName);
  } else if (filePathOrName) {
    const baseName = path.basename(filePathOrName.replace(/\\/g, "/"));
    const matches = await vscode.workspace.findFiles(`**/${baseName}`, "**/{node_modules,.git,Debug,Release,build}/**", 5);
    if (matches.length > 0) {
      targetUri = matches[0];
    }
  }

  if (!targetUri) {
    vscode.window.showWarningMessage(`Could not locate file in workspace: ${filePathOrName}`);
    return;
  }

  try {
    const doc = await vscode.workspace.openTextDocument(targetUri);
    const editor = await vscode.window.showTextDocument(doc, { preview: false });
    const ln = Math.max(0, (parseInt(lineNumber, 10) || 1) - 1);
    const pos = new vscode.Position(ln, 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  } catch (err) {
    vscode.window.showErrorMessage(`Failed to open ${filePathOrName}: ${err.message}`);
  }
}

class ChatViewProvider {
  constructor(extensionUri) {
    this._extensionUri = extensionUri;
    this._view = null;
  }

  resolveWebviewView(webviewView) {
    this._view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this._getHtml();

    webviewView.webview.onDidReceiveMessage(async (msg) => {
      try {
        if (msg.type === "ready" || msg.type === "refresh") {
          await this._pushStatus();
        } else if (msg.type === "indexWorkspace") {
          await this.indexCurrentWorkspace();
        } else if (msg.type === "browseModel") {
          await this.selectCustomModel();
        } else if (msg.type === "selectModel") {
          const res = await requestBackend("POST", "/api/models/select", {
            model_path: msg.modelPath,
            context_size: msg.contextSize ? parseInt(msg.contextSize, 10) : null,
            restart_server: true
          }, 60000);
          this._post({ type: "modelSwitched", result: res });
          await this._pushStatus();
        } else if (msg.type === "ask") {
          const editor = vscode.window.activeTextEditor;
          let currentFile = "";
          let currentLine = 1;
          let selectedCode = "";
          if (editor) {
            currentFile = editor.document.uri.fsPath || "";
            currentLine = editor.selection.active.line + 1;
            if (!editor.selection.isEmpty) {
              selectedCode = editor.document.getText(editor.selection);
            }
          }
          const res = await requestBackend("POST", "/api/query", {
            question: msg.question,
            mode: msg.mode || "reduce",
            current_file: currentFile,
            current_line: currentLine,
            selected_code: selectedCode,
            language: msg.language || "en"
          }, 780000);
          this._post({ type: "queryResult", result: res });
        } else if (msg.type === "openLocation") {
          await openFileAtLine(msg.file, msg.line);
        }
      } catch (err) {
        this._post({ type: "error", message: err.message });
      }
    });
  }

  _post(data) {
    if (this._view) {
      this._view.webview.postMessage(data);
    }
  }

  async _pushStatus() {
    try {
      const [health, models] = await Promise.all([
        requestBackend("GET", "/api/health", null, 3000),
        requestBackend("GET", "/api/models", null, 5000)
      ]);
      this._post({ type: "status", health, models });
    } catch (err) {
      this._post({ type: "offline", message: err.message });
    }
  }

  async indexCurrentWorkspace() {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      vscode.window.showWarningMessage("Open a workspace folder first.");
      return;
    }
    const wsPath = folders[0].uri.fsPath;
    this._post({ type: "indexingStarted", path: wsPath });
    try {
      const res = await requestBackend("POST", "/api/index", { path: wsPath }, 180000);
      this._post({ type: "indexingDone", result: res });
      await this._pushStatus();
    } catch (err) {
      this._post({ type: "error", message: `Indexing failed: ${err.message}` });
    }
  }

  async selectCustomModel() {
    const uris = await vscode.window.showOpenDialog({
      canSelectMany: false,
      openLabel: "Load GGUF Model",
      filters: { "GGUF Models": ["gguf"], "All Files": ["*"] }
    });
    if (!uris || uris.length === 0) return;
    const chosen = uris[0].fsPath;
    this._post({ type: "modelSwitching", path: chosen });
    try {
      const res = await requestBackend("POST", "/api/models/select", {
        model_path: chosen,
        restart_server: true
      }, 60000);
      this._post({ type: "modelSwitched", result: res });
      await this._pushStatus();
    } catch (err) {
      this._post({ type: "error", message: `Model switch failed: ${err.message}` });
    }
  }

  askWithSelection(text) {
    if (this._view) {
      this._view.show(true);
      this._post({ type: "prefill", text: `Explain this code:\n${text}` });
    }
  }

  _getHtml() {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
  body { font-family: var(--vscode-font-family); font-size: 12.5px; color: var(--vscode-foreground); background: var(--vscode-sideBar-background); margin: 0; padding: 10px; display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; gap: 8px; }
  .row { display: flex; gap: 6px; align-items: center; }
  select, input, textarea, button { font-family: inherit; font-size: 12px; border-radius: 4px; }
  select, input, textarea { background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, #3c3c3c); padding: 5px 7px; }
  button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 6px 10px; cursor: pointer; font-weight: 600; }
  button:hover { background: var(--vscode-button-hoverBackground); }
  button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  button:disabled { opacity: 0.55; cursor: not-allowed; }
  .status { font-size: 11px; color: var(--vscode-descriptionForeground); display: flex; justify-content: space-between; }
  #chat { flex: 1; overflow-y: auto; border: 1px solid var(--vscode-panel-border, #333); border-radius: 4px; padding: 8px; background: var(--vscode-editor-background); line-height: 1.45; }
  .msg { margin-bottom: 10px; white-space: pre-wrap; word-break: break-word; }
  .msg-user { color: var(--vscode-textLink-foreground); font-weight: 600; }
  .msg-sys { color: var(--vscode-descriptionForeground); font-style: italic; }
  .cite-link { color: var(--vscode-textLink-foreground); text-decoration: underline; cursor: pointer; font-family: var(--vscode-editor-font-family, monospace); }
  #citations { max-height: 130px; overflow-y: auto; border: 1px solid var(--vscode-panel-border, #333); border-radius: 4px; padding: 4px; font-size: 11.5px; display: none; }
  .cit-item { padding: 3px 5px; cursor: pointer; border-radius: 3px; }
  .cit-item:hover { background: var(--vscode-list-hoverBackground); }
  textarea { width: 100%; box-sizing: border-box; resize: vertical; min-height: 54px; }
</style>
</head>
<body>
  <div class="status">
    <span id="lblStatus">Checking backend...</span>
    <span id="lblStats"></span>
  </div>

  <div class="row">
    <button id="btnIndex" style="flex:1">Index Workspace</button>
    <select id="selLang" style="width:65px">
      <option value="en">EN</option>
      <option value="ru" selected>RU</option>
      <option value="he">HE</option>
    </select>
  </div>

  <div class="row">
    <select id="selModel" style="flex:1"></select>
    <button id="btnBrowse" class="secondary" title="Select local .gguf file">Browse...</button>
  </div>

  <div class="row">
    <select id="selMode" style="flex:1">
      <option value="reduce" selected>Reducing AI (5m)</option>
      <option value="reduce_deep">Deep Reducing (10m)</option>
      <option value="fast">Fast RAG</option>
      <option value="callers">Call Graph (Callers)</option>
      <option value="definition">Find Definition</option>
      <option value="grep">FTS5 Grep</option>
    </select>
  </div>

  <div id="chat"></div>
  <div id="citations"></div>

  <textarea id="txtQuestion" placeholder="Ask about architecture, functions, or Ctrl+Enter to send..."></textarea>
  <button id="btnSend">Send</button>

  <script>
    const vscode = acquireVsCodeApi();
    const lblStatus = document.getElementById("lblStatus");
    const lblStats = document.getElementById("lblStats");
    const btnIndex = document.getElementById("btnIndex");
    const selLang = document.getElementById("selLang");
    const selModel = document.getElementById("selModel");
    const btnBrowse = document.getElementById("btnBrowse");
    const selMode = document.getElementById("selMode");
    const chat = document.getElementById("chat");
    const citations = document.getElementById("citations");
    const txtQuestion = document.getElementById("txtQuestion");
    const btnSend = document.getElementById("btnSend");

    function escapeHtml(s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function linkifyCitations(text) {
      const escaped = escapeHtml(text || "");
      return escaped.replace(/\\[([A-Za-z0-9_.\\-\\\\/]+\\.(?:cpp|c|cc|cxx|h|hpp|hxx|hh|inl|cs|py)):(\\d+)\\]/g, (m, f, l) => {
        return '<span class="cite-link" data-file="' + f.replace(/"/g, "&quot;") + '" data-line="' + l + '">[' + f + ':' + l + ']</span>';
      });
    }

    function appendMsg(cls, prefix, body, linkify = false) {
      const div = document.createElement("div");
      div.className = "msg " + cls;
      div.innerHTML = "<strong>" + escapeHtml(prefix) + "</strong>\\n" + (linkify ? linkifyCitations(body) : escapeHtml(body));
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    chat.addEventListener("click", (e) => {
      const t = e.target;
      if (t.classList && t.classList.contains("cite-link")) {
        vscode.postMessage({ type: "openLocation", file: t.dataset.file, line: parseInt(t.dataset.line, 10) });
      }
    });

    btnIndex.addEventListener("click", () => vscode.postMessage({ type: "indexWorkspace" }));
    btnBrowse.addEventListener("click", () => vscode.postMessage({ type: "browseModel" }));
    selModel.addEventListener("change", () => {
      if (selModel.value) {
        appendMsg("msg-sys", "System", "Switching model (loading GGUF)...");
        vscode.postMessage({ type: "selectModel", modelPath: selModel.value });
      }
    });

    function sendQuery() {
      const q = txtQuestion.value.trim();
      if (!q) return;
      txtQuestion.value = "";
      appendMsg("msg-user", "You", q);
      btnSend.disabled = true;
      btnSend.textContent = "Running...";
      citations.style.display = "none";
      citations.innerHTML = "";
      vscode.postMessage({ type: "ask", question: q, mode: selMode.value, language: selLang.value });
    }

    btnSend.addEventListener("click", sendQuery);
    txtQuestion.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendQuery();
      }
    });

    window.addEventListener("message", (event) => {
      const msg = event.data;
      if (msg.type === "status") {
        lblStatus.textContent = msg.health.qwen_online ? "Online (LLM Ready)" : "Backend Online (LLM Offline)";
        lblStats.textContent = msg.health.files_indexed + " files / " + msg.health.calls_indexed + " calls";
        selModel.innerHTML = "";
        (msg.models.local_models || []).forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.path;
          opt.textContent = m.name + " (" + m.size_gb + " GB)";
          if (m.active) opt.selected = true;
          selModel.appendChild(opt);
        });
      } else if (msg.type === "offline") {
        lblStatus.textContent = "Backend Offline (run start_assistant.bat)";
      } else if (msg.type === "indexingStarted") {
        btnIndex.disabled = true;
        appendMsg("msg-sys", "Indexer", "Indexing " + msg.path + " ...");
      } else if (msg.type === "indexingDone") {
        btnIndex.disabled = false;
        const r = msg.result;
        appendMsg("msg-sys", "Indexer", r.success ? ("Indexed " + r.files_count + " files, " + r.symbols_count + " symbols in " + r.duration_seconds + "s") : ("Error: " + r.message));
      } else if (msg.type === "modelSwitched") {
        appendMsg("msg-sys", "Model Manager", msg.result.message || "Model updated");
      } else if (msg.type === "queryResult") {
        btnSend.disabled = false;
        btnSend.textContent = "Send";
        const r = msg.result;
        if (r.success) {
          appendMsg("", "Agent", r.answer, true);
          if (r.citations && r.citations.length > 0) {
            citations.style.display = "block";
            r.citations.forEach((c) => {
              const row = document.createElement("div");
              row.className = "cit-item";
              const shortName = (c.file || "").split(/[\\\\/]/).pop();
              row.textContent = "[" + shortName + ":" + c.line + "] " + (c.symbol || "");
              row.addEventListener("click", () => {
                vscode.postMessage({ type: "openLocation", file: c.file, line: c.line });
              });
              citations.appendChild(row);
            });
          }
        } else {
          appendMsg("msg-sys", "Error", r.error || "Unknown error");
        }
      } else if (msg.type === "prefill") {
        txtQuestion.value = msg.text;
        txtQuestion.focus();
      } else if (msg.type === "error") {
        btnSend.disabled = false;
        btnSend.textContent = "Send";
        btnIndex.disabled = false;
        appendMsg("msg-sys", "Error", msg.message);
      }
    });

    vscode.postMessage({ type: "ready" });
  </script>
</body>
</html>`;
  }
}

function activate(context) {
  const provider = new ChatViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("offlineCppAgent.chatView", provider, {
      webviewOptions: { retainContextWhenHidden: true }
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("offlineCppAgent.indexWorkspace", () => provider.indexCurrentWorkspace())
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("offlineCppAgent.selectModelFile", () => provider.selectCustomModel())
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("offlineCppAgent.askSelection", () => {
      const ed = vscode.window.activeTextEditor;
      if (ed && !ed.selection.isEmpty) {
        provider.askWithSelection(ed.document.getText(ed.selection));
      }
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
