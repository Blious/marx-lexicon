# 本地开发与远端同步

## 每次更改必须推送

每次完成一轮更改后，必须提交并推送到远端，代码、数据、配置和文档修改均适用。除非用户明确要求仅保留本地，否则任务完成意味着相关更改已提交、推送，并确认本地分支与远端分支同步。该要求也适用于对本开发文档的修改。

本项目远端为 `https://github.com/Blious/marx-lexicon.git`，默认开发分支为 `main`，提交和推送使用 `Blious` 身份。使用其他开发分支时推送到其对应远端分支。

## 开发流程

1. 查看工作区和当前分支，保留已有修改，明确本轮更改范围。
2. 完成修改，并按影响范围执行必要检查。代码变更运行相关测试；数据变更执行 `validate` 和 `build`；仅修改文档时检查内容和差异即可。无需为低风险修改增加无关测试、哈希校验或逐次备份。
3. 检查提交差异，将本轮相关文件提交。`build/` 中的本地数据库、导出结果以及其他被忽略文件不纳入提交。
4. 推送当前分支到 `origin`，确认推送成功且没有待推送提交。完成说明中给出分支和提交记录。

常用命令（默认分支为 `main`）：

```powershell
git status --short --branch
git diff
python -m marx_lexicon validate
python -m marx_lexicon build
python -m pytest
git add <本轮相关文件>
git diff --cached --check
git commit -m "描述本轮更改"
git push origin main
git status --short --branch
```

按变更范围选择上述检查命令，不要求每次全部运行。首次推送新分支时使用 `git push -u origin <分支名>`。

遇到认证、网络故障或远端冲突时，应先尝试在现有授权内解决；无法完成时，明确报告失败原因、本地提交及尚未同步的内容。不得将本地提交当作远端同步成功，也不要通过强制推送覆盖远端历史。
