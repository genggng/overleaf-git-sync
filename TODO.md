# TODO

1. 修改 `ol-ce-sync` 命令名称，目前太长了，例如改成 `ol`。
2. `ol-ce-sync pull` 时不要直接合并，先放到暂存区。并且要求 `pull` 之前工作区必须是干净的，否则执行 `pull` 报警。
3. `ol-ce-sync init` 时自动创建 `.gitignore`，并把 LaTeX 常见的中间文件后缀都加进去，尤其把 `.ol-sync` 目录加进去。
4. 需要在 `README.md` 中描述每条命令的详细解释。
