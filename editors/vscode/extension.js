// Kite VS Code 扩展：启动 kitec lsp 作为语言服务器
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const cfg = vscode.workspace.getConfiguration("kite");
  // 优先用设置里的 kitec；否则用扩展自带的（server/bin/kitec，由 build.sh 打包），最后找 PATH 里的 kitec
  const bundled = path.join(context.extensionPath, "server", "bin", "kitec");
  const kitec = cfg.get("kitecPath") || (fs.existsSync(bundled) ? bundled : "kitec");

  client = new LanguageClient(
    "kite",
    "Kite Language Server",
    { command: kitec, args: ["lsp"], transport: TransportKind.stdio },
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
