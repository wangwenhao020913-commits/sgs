# packaging · Windows 便携版打包配方

把 `SGS` 打成一个**不需要装 Python** 的 Windows x64 文件夹（`SGS-win64`，含 `SGS.exe`），
双击即用。这里的所有文件都是**新增**的：`src/`、`tools/`、`tests/`、`docs/`、`pyproject.toml`
一个字节都没有被改动。

| 产物 | 说明 |
|---|---|
| `dist/SGS-win64/SGS.exe` | 入口（console 子系统）：无敌启动 = 起本地服务 + 开浏览器；带参 = 命令行跑一局 |
| `dist/SGS-win64/_internal/` | 解释器 + 字节码 + `static/` + `sgs/data/files/` + `build-info.json` |
| `dist/SGS-win64/使用说明.txt` | 给最终用户的中文说明（版本号由构建脚本填入） |
| `dist/SGS-win64/NOTICE.txt` | 第三方内容清单（含随包的 40 条 OL 语音） |
| `dist/SGS-win64/LICENSE.txt` | MIT 全文（MIT 要求随副本分发） |
| `dist/SGS-win64-<HEAD>-<日期>.zip` | 分发用的压缩包（`dist/` 已在 `.gitignore` 里） |

## 一条命令重建

在 **Windows** 上（PowerShell，不需要管理员）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1 -Version <git 短 HEAD>
```

或者从 WSL 经 interop 调用（本项目实际用法）：

```bash
cd /mnt/c && /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
  -NoProfile -ExecutionPolicy Bypass \
  -File '\\wsl.localhost\Ubuntu\home\admin2026\dsh_workspace\SGS\packaging\build.ps1' \
  -Version "$(git rev-parse --short HEAD)"
```

打包后自检（真起 exe、真打 HTTP、真关进程）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\selfcheck.ps1
```

`build.ps1` 会在 `%USERPROFILE%\sgs-build` 里自备一切：用户级 Python 3.14.4、
构建 venv、staging 树、PyInstaller 中间产物。**不需要**管理员权限，**不写**系统 PATH，
构建完删掉这个目录就等于回到干净状态。

## 为什么是现在这个形状（决策记录）

| 决定 | 理由 |
|---|---|
| **onedir**（文件夹）而不是 onefile | onefile 每次启动都要把整包解到 `%TEMP%` 再跑，崩溃时还留下 `_MEIxxxx` 残留目录；文件夹形态启动是瞬时的，`static/` 也还留在磁盘上可查。代价只是要发 zip。 |
| **console=True** | `--noconsole` 下 `sys.stdout` 是 `None`，`BaseHTTPRequestHandler.log_message` 会写它 ⇒ **每个请求静默失败**（PyInstaller #8952）。这个包是"用户双击、看到黑窗口、关窗口即退出"的形态，console 是对的。 |
| **数据放 `%LOCALAPPDATA%\SGS`** | 源码树里 `serve.py` 的 `DEFAULT_RUNS_DIR` 是从 `__file__` 推出来的（`REPO_ROOT / "runs"`），冻结包会指到 `_internal` 里；启动器**显式**把 `runs_dir` 传给 `make_server()`，所以一行源码都不用改。也因此 exe 旁边的文件夹永远只读、可以整体替换。 |
| **单实例靠 HTTP 探活** | `server.json` 记 `{port, pid, url}`，但**判据是 HTTP 而不是 pid**：进程崩溃后 pid 会被复用，文件也会假活。探活还校验响应体里有 `"decks"` 这个键——只认状态码 200 不够，端口会被别的程序复用。`selfcheck.ps1` 的回合 2 专门验这条（合成一份死端口 + 死 pid 的陈旧文件，必须不挡启动）。 |
| **端口 8100 优先，然后在 8101–8109 里顺延，最后才交给内核** | 固定端口最省事，但"被占就起不来"是最常见的支持问题；直接甩给内核又会让**每次启动地址都变**、书签作废。顺延几格让地址基本稳定。实测这台机器上 8100 被 WSL 侧的服务占着，exe 自己落到了 8101。 |
| **zip 自己逐条写**，不用 `Compress-Archive` / `ZipFile.CreateFromDirectory` | 这两个在 **.NET Framework**（PowerShell 5.1）下都会把条目名写成反斜杠 `SGS-win64\_internal\…`，那不是合法的 zip 布局：资源管理器能忍，7-Zip / WinRAR / 其他系统的 `unzip` 会解错。所以逐文件 `CreateEntryFromFile` 并显式把分隔符换成 `/`，同时保留顶层 `SGS-win64/` 目录。 |
| **随包保留 40 条 OL 卡牌语音** | 用户 2026-11-14 明确选择「保留」（本可排除以缩小包体、规避第三方音频转发）。`NOTICE.txt` 里写明了这一点与"权利人要求即移除"的口径。 |
| **两个 `.ps1` 纯 ASCII、无 BOM** | Windows PowerShell 5.1 用 **ANSI 代码页**解码无 BOM 的脚本：UTF-8 中文注释会变乱码，解析器吐出的是乱码报错（本项目真踩过两次）。中文全部放 `使用说明.txt` / `NOTICE.txt` / 本文件，以及 exe 自己的控制台输出。 |
| **不在 CI 里打 exe** | `tests/test_dod.py::test_there_is_no_second_gate_entry` 要求 `.github/workflows/` 下**每个** `.yml` 都调用 `bash tests/run_all.sh`；为打包单开一条 workflow 会白跑 8 分钟门禁。exe 是发布产物，不是门禁判据。 |
| **不往 `tests/` 加测试** | 用户的口径是"不动源码"。打包后的验证由 `selfcheck.ps1` 承担（它测的是产物，不是源码）。`packaging/` 在 `tools/gate-staged.sh` 的分级里是 **skip**，所以提交它不会触发全量门禁。 |

## 自检覆盖了什么、没覆盖什么

`selfcheck.ps1` 无头能证明的（每项一条 `PASS/FAIL`，本机实测 **47 条全过**）：

1. `_internal/` 里真的有 `sgs_web/static/`（含 `voices/` 41 条）、`sgs_web/descriptions.json`、
   `sgs/data/files/*.json`、`python314.dll`、`VCRUNTIME140.dll`、`build-info.json`，且**没有**
   `.py` 源码泄漏（`noarchive=False` 意味着只发字节码）；
2. **回合 1（干净启动，`--port 0`）**：`server.json` 出现 → `GET /` 是开局页 → 5 个静态资源 +
   4 个 API 全 200 → `POST /api/sessions`（8 人 / `standard_ex_160`）→ 拿到挂起询问 → 提交一步
   应答 200（回包带 `records`）→ `server.log` 落盘；杀进程后断言**进程真的没了**、且没有
   监听器还挂在它的 pid 上；
3. **回合 2（陈旧状态）**：自己写一份"死端口 65000 + 死 pid 999999"的 `server.json`，再启动
   必须能起来且**不复用那个死端口**；
4. **回合 3（默认端口 + 单实例）**：不带参数启动（走 8100→8109 的梯子）能落到一个可答端口；
   此时再点一次 exe 必须**自己退出（退出码 0）**，而第一个实例继续服务。

> 为什么不断言"端口已释放"：在 WSL2 的镜像网络里，Windows 进程用过的端口会被 `wslrelay`
> 继续持有成 LISTEN 状态——这条断言在 WSL 里恒假、在真机上却是正常的。断言"进程退出 +
> 该 pid 名下无监听"才是我们真正控制的那个事实。这也是"自检必须与环境无关"的一次教训。

无头**证明不了**、只能人眼看的：

- SmartScreen 首次弹窗（未签名 exe 必然出现，点「更多信息 → 仍要运行」）；
- 浏览器真的被拉起来；
- 点控制台窗口的 × 之后屏幕上的网页失效（这条是"关窗口即退出"的语义）。

## 已知限制

- **只出 Windows x64**。PyInstaller 不能交叉编译，Linux/macOS 要各自构建。
- **未签名**：SmartScreen 会警告，且每个新版本都要重新积累信誉。真要消除得买代码签名
  （Microsoft Artifact Signing 约 $10/月，EV 证书也不能绕过 SmartScreen）。
- **构建机必须是 3.14.x**：脚本硬拒其它版本（`-PythonVersion` 默认 3.14.4，与开发用 `.venv`
  同版本）。这就是"3.14 限制"在打包后的残留形态——**只留在构建机，不在用户那侧**。
- **崩溃后 `server.json` 会陈旧**：这是设计（`selfcheck.ps1` 的回合 2 保证它能自愈），
  不是可以"优化"掉的缓存。

## 排障

| 症状 | 多半是 |
|---|---|
| `表达式或语句中包含意外的标记` 之类的乱码报错 | `.ps1` 丢了 BOM **或**被写成了 UTF-8 中文。用纯 ASCII 重写，别给 `Write-Host` 加中文。 |
| `Failed to allocate PyConfig structure` | PyInstaller < 6.15.0 配 Python 3.14；或 `PYTHONPATH` 指向了别的解释器。 |
| 构建卡在 pip | 换镜像：`-PyInstallerVersion` 之外先手动 `pip install`，或设 `PIP_INDEX_URL`。 |
| 双击后"已保护你的电脑" | 未签名，正常。真机验收时点「仍要运行」。 |

## 最近一次成功构建（本机实测）

| 项 | 值 |
|---|---|
| 版本戳 | `f1839c6`（git 短 HEAD，构建时传入） |
| 构建时刻 | 2026-11-13，Python **3.14.4** / PyInstaller **6.22.3** / pydantic **2.13.5** |
| 构建机 | Windows 10 22H2 x64；构建区 `C:\Users\asus\sgs-build`（含用户级 Python 3.14.4，未改 PATH、未装给所有用户） |
| 目录 | **29.4 MB**（`SGS.exe` 5.3 MB），`_internal/` 里 41 条语音 + 17 个数据 JSON |
| zip | **14.4 MB**，147 条目、顶层 `SGS-win64/`、反斜杠 0 条、CRC 全通过 |
| 自检 | **47 项 PASS / 0 FAIL** |
| 端到端 | 起 exe → 8 人 `standard_ex_160` 建局 → 拿到 `RESPOND` 挂起 → 提交一步应答 → 回包 337 条记录 |
