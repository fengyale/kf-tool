# TRAE Work / WorkBuddy 每日签到自动化

给 **TRAE Work**（新版 Trae CN / 旧版 TRAE SOLO CN 均支持）和 **WorkBuddy** 做每日自动签到领积分。**装一次，以后每天全自动，什么都不用管。**

> WorkBuddy 与 CodeBuddy CN 积分通用，只签 WorkBuddy 一个即可。两个客户端相互独立：装一个、签一个；都装就都签；只装其中一个也完全没问题。

## 三个核心特性

1. **登录一次就够**
   签到借用客户端自己的登录态。安装前登录过一次账号即可，之后 token 过期客户端会自己续，永远不需要重新登录。

2. **程序开不开都能签**
   - 客户端没开 → 自动拉起、签到、自动关闭（全程无窗口打扰，你看不到它动）
   - 客户端开着 → 直接接入签到，程序毫无感知
   - 客户端开着但缺调试参数 → 自动重启补上，签到后恢复运行
   - 电脑重启也不影响，定时任务是系统级的

3. **零手动配置**
   客户端装在哪、Node 装在哪、定时任务怎么注册——`install.ps1` 全部自动探测和处理。**正常情况下不需要手动改任何路径和配置**，只有自动探测失败时脚本才会提示你手动指定。

## 实现方式（原理简介）

不碰账号、不存凭证，签到请求全部由客户端自己发出：

- **TRAE**：以 `--remote-debugging-port=9222`（仅绑 127.0.0.1）接入渲染进程，直接调用客户端内部签到端口的 `fetchStatus()` / `claim()`。
- **WorkBuddy**：以 Electron 原生 `--inspect=127.0.0.1:9229` 接入**主进程**，经由主进程自己的 IPC 转发器调用 `auth:getCheckinStatus` / `auth:claimDailyCheckin`。已签到当天重复领取返回「今天已签到」，接口幂等。

## 环境要求

- Windows 10 / 11
- [Node.js](https://nodejs.org/zh-cn) 22 及以上（官网下 LTS 版本，一路下一步装完即可；命令行输入 `node -v` 能看到版本号即成功）
- TRAE 和/或 WorkBuddy 已安装，并且**手动登录过一次账号**

## 快速开始（共 3 步）

```
第 1 步：解压压缩包到任意目录（如 D:\autoSign，整个文件夹放哪都行）
第 2 步：安装 Node.js 22+（已装过可跳过）
第 3 步：在解压出来的文件夹里打开 PowerShell，运行：
         powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**怎么"在文件夹里打开 PowerShell"：** 进入解压后的文件夹（能看到 install.ps1 的那一层），在地址栏输入 `powershell` 后回车，然后粘贴第 3 步的命令即可。（Windows 11 也可在文件夹空白处右键 →「在终端中打开」。）

`install.ps1` 会自动完成：检查 Node → 探测客户端（两个都探，没装的自动跳过）→ 生成 `config.json` → 修补快捷方式（原文件自动备份为 `.lnk.orig`）→ 注册每天 00:30 的定时任务 → 冒烟测试。

看到最后显示 **"冒烟测试通过"** 即安装成功，此后每天自动签到，无需再操作。

## 常用参数（一般用不上）

```powershell
# 只装其中一个客户端的签到（默认两个都探）
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Apps trae
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Apps workbuddy

# 客户端装在特殊位置、自动探测失败时手动指定
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Exe "D:\某目录\Trae CN.exe"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -WorkBuddyExe "D:\某目录\WorkBuddy.exe"

# 修改每天签到时间（默认 00:30）
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Time "09:00"
```

## 手动跑一次（可选）

```powershell
node src\run-daily.mjs             # 立即签全部已配置客户端（当天已签会自动跳过）
node src\run-daily.mjs trae        # 只签 TRAE
node src\run-daily.mjs workbuddy   # 只签 WorkBuddy
node src\run-daily.mjs --dry-run   # 只查看签到状态，不领取
```

## 查看签到结果

打开 `logs\sign.log`，每次执行追加一条记录，行首带客户端标签一眼可辨：

```
[2026/9/30 00:30:01] [TRAE] [结果] ok=true 签到前=false 已领取=true 签到后=true
[2026/9/30 00:30:04] [WorkBuddy] [结果] ok=true 签到前=false 已领取=true 签到后=true
```

`已领取=true` 表示当天签到成功。两个客户端的接口都自带防重复保护，重复执行不会重复领取。

## 常见问题

**只装了一个客户端，会有影响吗？**
没有。安装时默认探测两个，探不到的自动跳过，只配置机器上实际安装的；之后每天只签已配置的客户端。即使日后卸载了其中一个，任务也会自动跳过它（日志记一行 `[跳过]`），另一个照常签到、整体不算失败。

**杀毒软件 / Windows 提示风险？**
脚本只做：本地起调试端口、注册系统定时任务、改快捷方式参数，不联网上传任何数据。部分安全软件可能对"注册定时任务/修改快捷方式"敏感，允许即可；不放心可直接阅读全部源码（`src` 目录很小，无第三方依赖）。

**客户端升级后还能用吗？**
能。升级可能重置快捷方式，重跑一次 `install.ps1` 即可；就算忘了，定时任务的自愈逻辑也会兜底。

**日志报"未找到 CheckinCreditsPort"（TRAE）或"未找到 wb:invoke handler"（WorkBuddy）？**
客户端大版本更新导致内部结构变化，把 `logs\sign.log` 内容发给维护者适配。

**怎么完全卸载？**
管理员 PowerShell 运行 `Unregister-ScheduledTask -TaskName "TRAE-Solo-CN-DailyCheckin" -Confirm:$false`，把各快捷方式的 `.lnk.orig` 覆盖回 `.lnk`，删除解压文件夹即可，系统无其他残留。

## 压缩包内容

```
install.ps1            一键安装脚本（入口）
README.md              本说明
src\run-daily.mjs      每日签到入口（定时任务执行它）
src\runner.mjs         通用调度（拉起/重启/重试/日志）
src\cdp.mjs            极简 CDP 客户端
src\apps\trae.mjs      TRAE 适配器
src\apps\workbuddy.mjs WorkBuddy 适配器
src\sign.mjs           兼容旧版的单 TRAE 入口
```

`config.json` 和 `logs\` 不随包分发，首次运行 install.ps1 时自动生成。

## 安全说明

- 脚本**不获取、不存储任何账号凭证**，签到请求由客户端自己发出
- 调试端口 / inspector 只绑定本机（127.0.0.1），外网无法访问
- 操作对象是你自己的账号，属个人自动化；非官方调用方式存在理论上的风控变数，请自行评估使用
