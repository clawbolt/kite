// Kite VS Code 扩展：启动 kite/lsp.py 作为语言服务器
const path = require("path");
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const cfg = vscode.workspace.getConfiguration("kite");
  const python = cfg.get("pythonPath") || "python3";
  const server = cfg.get("serverPath") || path.join(context.extensionPath, "server", "lsp.py");

  client = new LanguageClient(
    "kite",
    "Kite Language Server",
    { command: python, args: [server], transport: TransportKind.stdio },
    { documentSelector: [{ scheme: "file", language: "kite" }] }
  );
  client.start();

  context.subscriptions.push(
    vscode.workspace.onWillSaveTextDocument((e) => {
      if (e.document.languageId !== "kite") return;
      if (!vscode.workspace.getConfiguration("kite").get("formatOnSave")) return;
      e.waitUntil(vscode.commands.executeCommand("vscode.executeFormatDocumentProvider", e.document.uri,
        { tabSize: 2, insertSpaces: true }));
    })
  );
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
