# Kite for VS Code

语法高亮、实时类型诊断、悬停文档、跳转定义、大纲、补全、保存时格式化。
语言服务器是 `kitec lsp`（不需要 Python）：build.sh 会把 bin/kitec 连同 prelude、runtime、std 打包进扩展；
也可以在设置 `kite.kitecPath` 里指定自己的 kitec。
