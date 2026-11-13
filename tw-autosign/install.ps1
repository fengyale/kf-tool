# TRAE Work / WorkBuddy 自动签到 —— 新机器一键安装
# 做四件事：探测各客户端路径并写 config.json / 修补快捷方式 / 注册每天定时任务 / 冒烟测试
#
# 运行方式（在项目根目录）：
#   powershell -ExecutionPolicy Bypass -File install.ps1
# 可选参数：
#   -Apps trae|workbuddy|all      安装哪些客户端的签到（默认 all，探测不到的会跳过并提示）
#   -Exe "D:\xxx\TRAE SOLO CN.exe"          手动指定 TRAE 路径（自动探测失败时用）
#   -WorkBuddyExe "D:\xxx\WorkBuddy.exe"   手动指定 WorkBuddy 路径
#   -Time "09:00"                           修改每日签到时间（默认 00:30）
#   -SkipSmokeTest                          跳过最后的安装自检

param(
  [ValidateSet('trae', 'workbuddy', 'all')]
  [string]$Apps = 'all',
  [string]$Exe = '',
  [string]$WorkBuddyExe = '',
  [string]$Time = '00:30',
  [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'
# 项目根目录：优先 PSScriptRoot，其次调用参数，最后当前目录（兼容各种控制台启动方式）
$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Root) { $Root = (Get-Location).Path }
$TaskName = 'TRAE-Solo-CN-DailyCheckin'
Write-Host "项目目录: $Root" -ForegroundColor DarkGray

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# 各客户端的静态信息
$AppDefs = @(
  [ordered]@{
    id = 'trae'
    label = 'TRAE Work'
    port = 9222
    flag = '--remote-debugging-port=9222'
    nameRegex = { param($n) ($n -match 'trae' -and $n -match 'SOLO|WORK|\bCN\b') }
    exeNames = @('Trae CN.exe', 'TRAE SOLO CN.exe', 'TRAE Work CN.exe')
    manual = $Exe
    fallback = @(
      'F:\Programs\Trae CN\Trae CN.exe',
      (Join-Path $env:LOCALAPPDATA 'Programs\Trae CN\Trae CN.exe'),
      (Join-Path $env:ProgramFiles 'Trae CN\Trae CN.exe'),
      (Join-Path ${env:ProgramFiles(x86)} 'Trae CN\Trae CN.exe'),
      'F:\Programs\TRAE SOLO CN\TRAE SOLO CN.exe',
      (Join-Path $env:LOCALAPPDATA 'Programs\TRAE SOLO CN\TRAE SOLO CN.exe')
    )
  },
  [ordered]@{
    id = 'workbuddy'
    label = 'WorkBuddy'
    port = 9229
    flag = '--inspect=127.0.0.1:9229'
    nameRegex = { param($n) ($n -match 'WorkBuddy') }
    exeNames = @('WorkBuddy.exe')
    manual = $WorkBuddyExe
    fallback = @(
      'F:\Program Files\WorkBuddy\WorkBuddy.exe',
      (Join-Path $env:LOCALAPPDATA 'Programs\WorkBuddy\WorkBuddy.exe'),
      (Join-Path $env:ProgramFiles 'WorkBuddy\WorkBuddy.exe'),
      (Join-Path ${env:ProgramFiles(x86)} 'WorkBuddy\WorkBuddy.exe')
    )
  }
)

# ---------- 1. 检查 Node ----------
Step '检查 Node.js'
try {
  $nodePath = (Get-Command node -ErrorAction Stop).Source
  $ver = (& node --version) -replace '^v', ''
  $major = [int]($ver.Split('.')[0])
  if ($major -lt 22) { throw "需要 Node 22 及以上，当前 $ver" }
  Ok "Node $ver ($nodePath)"
} catch {
  Write-Host '未检测到 Node.js 22+。请先安装：https://nodejs.org/zh-cn (LTS 版本即可)' -ForegroundColor Red
  exit 1
}

# ---------- 2. 探测客户端 ----------
function Find-AppExe($def) {
  if ($def.manual) {
    if (-not (Test-Path $def.manual)) { throw "手动指定的路径不存在: $($def.manual)" }
    return $def.manual
  }
  $candidates = @()
  # 从注册表卸载信息里找 InstallLocation
  foreach ($root in @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
  )) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
      $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
      if ($p.DisplayName -and (& $def.nameRegex $p.DisplayName) -and $p.InstallLocation) {
        foreach ($n in $def.exeNames) { $candidates += (Join-Path $p.InstallLocation $n) }
      }
    }
  }
  $candidates += $def.fallback
  return ($candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1)
}

if ($Apps -eq 'all') { $wanted = @('trae', 'workbuddy') } else { $wanted = @($Apps) }
$found = [ordered]@{}
foreach ($def in $AppDefs) {
  if ($wanted -notcontains $def.id) { continue }
  Step "探测 $($def.label) 客户端路径"
  try {
    $exePath = Find-AppExe $def
  } catch {
    Warn $_
    continue
  }
  if (-not $exePath) {
    Warn "未找到 $($def.label)，跳过（可用 -$(@{$trae='Exe';$workbuddy='WorkBuddyExe'}[$def.id]) 参数手动指定路径后重跑）"
    continue
  }
  Ok "客户端: $exePath"
  $def.exePath = $exePath
  $found[$def.id] = $def
}
if ($found.Count -eq 0) {
  Write-Host '没有任何可用的客户端，安装中止。' -ForegroundColor Red
  exit 1
}

# ---------- 3. 写 config.json（多客户端结构，保留未涉及的已有配置） ----------
Step '写入 config.json'
$cfgPath = Join-Path $Root 'config.json'
$existingCfg = $null
if (Test-Path $cfgPath) {
  try { $existingCfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch { }
}
$cfgApps = [ordered]@{}
if ($existingCfg -and $existingCfg.apps) {
  foreach ($prop in $existingCfg.apps.PSObject.Properties) {
    $cfgApps[$prop.Name] = [ordered]@{ exe = [string]$prop.Value.exe; port = [int]$prop.Value.port }
  }
} elseif ($existingCfg -and $existingCfg.exe) {
  # 兼容最早期的单客户端 {exe, port} 结构
  $oldPort = 9222
  if ($existingCfg.port) { $oldPort = [int]$existingCfg.port }
  $cfgApps['trae'] = [ordered]@{ exe = [string]$existingCfg.exe; port = $oldPort }
}
foreach ($id in $found.Keys) {
  $d = $found[$id]
  $cfgApps[$id] = [ordered]@{ exe = [string]$d.exePath; port = [int]$d.port }
}
try {
  $json = @{ apps = $cfgApps } | ConvertTo-Json -Depth 5
  [System.IO.File]::WriteAllText($cfgPath, $json, [System.Text.UTF8Encoding]::new($false))
  Ok "已写入: $cfgPath（$($found.Keys -join ', ')）"
} catch {
  Write-Host ('写入 config.json 失败: ' + $_.Exception.Message) -ForegroundColor Red
  exit 1
}

# ---------- 4. 修补快捷方式（让用户日常点开的实例天然带调试参数，避免签到时重启打断） ----------
$shell = New-Object -ComObject WScript.Shell
$dirs = @(
  (Join-Path $env:USERPROFILE 'Desktop'),
  (Join-Path $env:PUBLIC 'Desktop'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
  (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')
)
foreach ($id in $found.Keys) {
  $d = $found[$id]
  Step "修补 $($d.label) 快捷方式（桌面 / 开始菜单中指向该 exe 的 .lnk，追加 $($d.flag)）"
  $patched = 0; $already = 0
  foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) { continue }
    Get-ChildItem $dir -Recurse -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
      try {
        $lnk = $shell.CreateShortcut($_.FullName)
        if ($lnk.TargetPath -and $lnk.TargetPath.ToLower() -eq $d.exePath.ToLower()) {
          if ($lnk.Arguments -like "*$($d.flag)*") { $already++; return }
          $backup = "$($_.FullName).orig"
          if (-not (Test-Path $backup)) { Copy-Item $_.FullName $backup -Force }
          $existing = if ($lnk.Arguments) { $lnk.Arguments.Trim() } else { '' }
          $lnk.Arguments = if ($existing) { "$existing $($d.flag)" } else { $d.flag }
          $lnk.Save()
          $patched++
          Ok "已修补: $($_.FullName)"
        }
      } catch { }
    }
  }
  Ok "本次修补 $patched 个，已带参数 $already 个"
}

# ---------- 5. 注册定时任务（无参数 = 签 config.json 中已配置的全部客户端） ----------
Step "注册定时任务 $TaskName（每天 $Time，顺序签 $($found.Keys -join ' / ')）"
$action   = New-ScheduledTaskAction -Execute $nodePath -Argument "`"$(Join-Path $Root 'src\run-daily.mjs')`"" -WorkingDirectory $Root
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description '每天自动完成 TRAE Work / WorkBuddy 每日签到' -Force | Out-Null
$t = Get-ScheduledTask -TaskName $TaskName
Ok "已注册: $($t.TaskName) / 状态 $($t.State)"

# ---------- 6. 冒烟测试 ----------
if (-not $SkipSmokeTest) {
  Step '冒烟测试（--dry-run，只查状态不领取；如客户端未运行会短暂拉起后关闭）'
  & $nodePath (Join-Path $Root 'src\run-daily.mjs') --dry-run
  if ($LASTEXITCODE -eq 0) { Ok '冒烟测试通过' } else { Write-Host '冒烟测试未通过，请把日志发给我: logs\sign.log' -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '安装完成。每天签到会自动进行，结果见 logs\sign.log（日志带 [TRAE] / [WorkBuddy] 标签）。' -ForegroundColor Cyan
Write-Host '快捷方式如被客户端更新还原，重新运行本脚本即可。' -ForegroundColor DarkGray
