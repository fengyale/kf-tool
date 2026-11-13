// 通用每日签到调度器（与具体客户端无关，差异由 apps/*.mjs 适配器提供）
// 职责：确保客户端可接入（未运行则临时拉起；运行中未带调试端口则重启补上），
//       调用适配器执行签到，写带 [TAG] 前缀的日志，最后恢复现场
//       （自己拉起的关掉；重启的保持运行）。

import { spawn } from 'node:child_process';
import { appendFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, basename } from 'node:path';
import { findTargets } from './cdp.mjs';

const STARTUP_TIMEOUT_MS = 180_000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function createLogger(tag, root) {
  const logDir = join(root, 'logs');
  const logFile = join(logDir, 'sign.log');
  return {
    file(line) {
      if (!existsSync(logDir)) mkdirSync(logDir, { recursive: true });
      const stamp = new Date().toLocaleString('zh-CN', { hour12: false });
      appendFileSync(logFile, `[${stamp}] [${tag}] ${line}\n`, 'utf8');
    },
    both(line) {
      this.file(line);
      console.log(`[${tag}] ${line}`);
    },
  };
}

export async function runForApp(app, { root, exe, port, dryRun, noLaunch }) {
  const log = createLogger(app.tag, root);
  const image = exe ? basename(exe) : app.defaultImage;
  const startedAt = Date.now();
  const deadline = startedAt + STARTUP_TIMEOUT_MS;
  let launchedByUs = false;      // 本来没开，我们拉起的（签到后关闭）
  let restartedForPort = false;  // 本来开着但没端口，我们重启的（签到后保持运行）

  /** 端口能连且已出现适配器认可的 target，才算真正可接入 */
  async function targetReady() {
    try {
      const list = await findTargets(port, app.readyTarget);
      return list.length > 0;
    } catch {
      return false;
    }
  }

  function appProcessCount() {
    return new Promise((resolve) => {
      const ps = spawn('tasklist', ['/FI', `IMAGENAME eq ${image}`, '/NH', '/FO', 'CSV'], {
        windowsHide: true,
      });
      let buf = '';
      ps.stdout.on('data', (d) => { buf += d.toString(); });
      ps.on('close', () => resolve((buf.match(new RegExp(image.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')) || []).length));
      ps.on('error', () => resolve(-1));
    });
  }

  function launchApp() {
    const child = spawn(exe, app.launchArgs(port), {
      detached: true,
      stdio: 'ignore',
      windowsHide: false,
      env: { ...process.env, ...app.launchEnv(port) },
    });
    child.unref();
  }

  async function closeApp({ graceful = false } = {}) {
    if (graceful) {
      // 不带 /F：向窗口发送关闭消息，给应用保存现场的机会
      await new Promise((resolve) => {
        const ps = spawn('taskkill', ['/IM', image, '/T'], { windowsHide: true });
        ps.on('close', resolve);
        ps.on('error', resolve);
      });
      for (let i = 0; i < 20; i++) {
        if ((await appProcessCount()) === 0) return true;
        await sleep(1000);
      }
      log.file('[重启] 优雅关闭超时，改用强制结束');
    }
    await new Promise((resolve) => {
      const ps = spawn('taskkill', ['/IM', image, '/T', '/F'], { windowsHide: true });
      ps.on('close', resolve);
      ps.on('error', resolve);
    });
    // 等进程真正退出，确认收尾干净
    for (let i = 0; i < 15; i++) {
      if ((await appProcessCount()) === 0) return true;
      await sleep(1000);
    }
    return false;
  }

  /** 调试端口 HTTP 是否可达（进程崩溃后端口即失联） */
  async function cdpAlive() {
    try {
      await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(3000) });
      return true;
    } catch {
      return false;
    }
  }

  /** 冷启动期间页面虽已就绪，业务容器可能尚未初始化，在截止时间内持续重试 */
  async function signWithRetry() {
    let last = null;
    let attempt = 0;
    let relaunches = 0;
    while (Date.now() < deadline) {
      attempt++;
      try {
        last = { report: await app.sign({ port, dryRun }) };
      } catch (e) {
        last = { report: { ok: false, error: e.message } };
      }
      if (last.report?.ok) return last;

      const errText = last.report?.error || '未知错误';
      log.file(`[重试] 第 ${attempt} 次未成功：${String(errText).slice(0, 140)}`);
      if (Date.now() + 3000 >= deadline) break;

      // 客户端可能在签到过程中崩溃（如 Windows 通知组件 wpnapps.dll 引发的 APPCRASH）：
      // 调试端口失联时清理后重新拉起，最多自救 2 次
      if (relaunches < 2 && Date.now() + 20000 < deadline && !(await cdpAlive())) {
        relaunches++;
        log.file(`[自救] 调试端口失联（客户端疑似崩溃退出），第 ${relaunches} 次清理并重新拉起`);
        await closeApp();
        launchApp();
        let rescued = false;
        while (Date.now() < deadline) {
          await sleep(2000);
          if (await targetReady()) { rescued = true; break; }
        }
        if (rescued) {
          log.file('[自救] 重新拉起成功，调试 target 已就绪');
          continue;
        }
        log.file('[自救] 重新拉起后仍未就绪');
        break;
      }

      await sleep(3000);
    }
    return last;
  }

  // ---------- 主流程 ----------
  if (!(await targetReady())) {
    const running = await appProcessCount();

    if (noLaunch) {
      log.both('[跳过] 指定了 --no-launch，且客户端未以调试端口运行');
      return 2;
    }

    if (!exe || !existsSync(exe)) {
      // 配置里有、但这台机器没装（或已卸载/路径失效）：跳过而非失败，
      // 这样只装一个客户端的机器不会产生失败记录，也不影响同批次其他客户端。
      log.both(`[跳过] 未找到 ${app.displayName} 客户端（配置路径: ${exe || '无'}），本次跳过`
        + '。若已安装请重跑 install.ps1，或用 --exe 指定路径。');
      return 2;
    }

    if (running > 0) {
      log.file('[重启] 客户端在运行但未带调试端口，关闭后将带端口重启（签到后保持运行）');
      const closed = await closeApp({ graceful: true });
      if (!closed) {
        // 存在杀不掉的残留进程（常见于提权/系统方式启动的实例）。
        // 不直接放弃：残留可能只是守护/辅助进程，不阻塞新实例，照常拉起试一次；
        // 若端口起不来，下方就绪超时会给出失败。
        const leftover = await appProcessCount();
        log.both(`[重启] ${leftover} 个进程无法结束（可能以管理员/系统方式启动），尝试直接拉起带端口实例`);
        restartedForPort = true; // 新实例签到后保持运行，替代被部分关闭的旧实例
      } else {
        await sleep(2000);
        restartedForPort = true;
      }
    }

    log.file(restartedForPort ? '[启动] 以调试端口重新拉起客户端' : '[启动] 客户端未运行，临时以调试端口拉起');
    launchApp();
    launchedByUs = !restartedForPort;

    let ready = false;
    while (Date.now() < deadline) {
      await sleep(2000);
      if (await targetReady()) { ready = true; break; }
    }
    if (!ready) {
      log.both('[失败] 启动超时：调试端口/页面未就绪');
      if (launchedByUs) await closeApp();
      return 1;
    }
    log.file('[就绪] 调试 target 已就绪');
  }

  const { report } = await signWithRetry();

  const summary = report
    ? `ok=${report.ok} 签到前=${report.checkedInBefore} 已领取=${report.claimed} 签到后=${report.checkedInAfter}`
      + (report.error ? ` 错误=${report.error}` : '')
    : '签到未返回结果';
  log.both(`[结果] ${summary}`);

  if (launchedByUs) {
    log.file('[收尾] 关闭临时拉起的客户端');
    const clean = await closeApp();
    log.file(clean ? '[收尾] 客户端已完全退出' : '[收尾] 警告：仍有残留进程');
  } else if (restartedForPort) {
    log.file('[收尾] 客户端保持运行（现为带调试端口实例，后续可直接接入）');
  }

  log.file(`[耗时] ${Date.now() - startedAt} ms`);
  return report?.ok ? 0 : 1;
}
