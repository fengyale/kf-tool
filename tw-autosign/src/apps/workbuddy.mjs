// WorkBuddy 适配器（主进程 Node inspector 方案）
//
// 原理：Electron 原生支持 --inspect=127.0.0.1:<port>，开启后主进程会在
//       /json/list 暴露一个 type=node 的 inspector target。连入后通过
//       process.mainModule.require('electron') 拿到 ipcMain，调用渲染进程的
//       通用转发 handler「wb:invoke」(event, channel, context, ...args)，
//       channel 即 auth:getCheckinStatus / auth:claimDailyCheckin。
//       请求由客户端主进程自身发出，账号鉴权信息本工具不接触、不存储。
//
// 已验证事实（WorkBuddy / Electron 37，2026-09-30）：
//   - status 返回 { active, today_checked_in, streak_days, daily_credit,
//                   total_credits, checkin_dates, week_progress, ... }
//   - 已签到当天再次 claim 返回 { code: 10001, msg: '今天已签到，请明天再来' }，
//     幂等、无副作用
//   - event 至少需要 { sender: <真实窗口 webContents> }（handler 要读 sender.id）
//
// 5.9.2 起新增主框架校验（resolveTrustedSubject）：
//   if (!e.senderFrame || e.senderFrame !== e.sender.mainFrame) throw PERMISSION_DENIED
//   故伪造的 event 必须同时带上 senderFrame = webContents.mainFrame（同一对象引用）。

import { findTargets, withTarget } from '../cdp.mjs';

/** 查询签到状态（只读） */
const STATUS_SCRIPT = `
(async () => {
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v)); } catch { return String(v); } };
  try {
    const electron = (process.mainModule && process.mainModule.require)
      ? process.mainModule.require('electron') : null;
    const ipcMain = electron && electron.ipcMain;
    const handlers = ipcMain && (ipcMain._invokeHandlers || ipcMain.invokeHandlers);
    const h = handlers && handlers.get('wb:invoke');
    if (typeof h !== 'function') {
      return JSON.stringify({ ok: false, error: '未找到主进程 wb:invoke handler（客户端版本可能已变更）' });
    }
    const win = electron.BrowserWindow.getAllWindows()[0];
    if (!win || !win.webContents) {
      return JSON.stringify({ ok: false, error: '主窗口尚未就绪' });
    }
    const wc = win.webContents;
    // senderFrame 必须与 sender.mainFrame 为同一对象，否则新版客户端抛 PERMISSION_DENIED
    const event = { sender: wc, senderFrame: wc.mainFrame };
    const raw = await h(event, 'auth:getCheckinStatus', null);
    return JSON.stringify({ ok: true, raw: safe(raw) });
  } catch (e) {
    return JSON.stringify({ ok: false, error: String((e && e.message) || e) });
  }
})()
`;

/** 执行领取（幂等：已签到当天返回 code=10001） */
const CLAIM_SCRIPT = `
(async () => {
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v)); } catch { return String(v); } };
  try {
    const electron = process.mainModule.require('electron');
    const handlers = electron.ipcMain._invokeHandlers || electron.ipcMain.invokeHandlers;
    const h = handlers && handlers.get('wb:invoke');
    if (typeof h !== 'function') {
      return JSON.stringify({ ok: false, error: '未找到主进程 wb:invoke handler（客户端版本可能已变更）' });
    }
    const win = electron.BrowserWindow.getAllWindows()[0];
    if (!win || !win.webContents) {
      return JSON.stringify({ ok: false, error: '主窗口尚未就绪' });
    }
    const wc = win.webContents;
    const event = { sender: wc, senderFrame: wc.mainFrame };
    const raw = await h(event, 'auth:claimDailyCheckin', null);
    return JSON.stringify({ ok: true, raw: safe(raw) });
  } catch (e) {
    return JSON.stringify({ ok: false, error: String((e && e.message) || e) });
  }
})()
`;

/** 连入主进程 node target 求值 */
async function evalOnMain(port, expression) {
  const targets = await findTargets(port, (t) => t.type === 'node' && t.webSocketDebuggerUrl);
  if (targets.length === 0) {
    throw new Error('未找到 type=node 的主进程 target，客户端可能未以 --inspect 启动');
  }
  const raw = await withTarget(targets[0], async (session) => {
    await session.send('Runtime.enable');
    return session.evaluate(expression);
  });
  return JSON.parse(raw);
}

/** WorkBuddy 状态字段固定为 today_checked_in */
function extractCheckedIn(raw) {
  if (raw == null || typeof raw !== 'object') return null;
  if (typeof raw.today_checked_in === 'boolean') return raw.today_checked_in;
  if (typeof raw.today_checked_in === 'number') return raw.today_checked_in === 1;
  return null;
}

/**
 * 解释 claim 返回：
 *   { __wbError: true }            → 客户端权限/服务层拒绝，失败
 *   code=10001                     → 今天已签到，视为成功（幂等）
 *   code 为其他非 0 数字            → 服务端业务失败
 *   无 code / code=0               → 领取成功
 */
function interpretClaim(raw) {
  if (!raw || typeof raw !== 'object') return { ok: true, already: false };
  if (raw.__wbError === true) {
    return { ok: false, error: `客户端拒绝: ${raw.message || raw.code || '未知原因'}` };
  }
  if (raw.code === 10001) return { ok: true, already: true };
  if (typeof raw.code === 'number' && raw.code !== 0) {
    return { ok: false, error: `领取返回 code=${raw.code} ${raw.msg || raw.message || ''}`.trim() };
  }
  return { ok: true, already: false };
}

export const workbuddyApp = {
  id: 'workbuddy',
  tag: 'WorkBuddy',
  displayName: 'WorkBuddy',
  defaultPort: 9229,
  defaultImage: 'WorkBuddy.exe',
  /** Electron 主进程 inspector（wb:invoke 只存在于主进程，渲染进程 CDP 用不上） */
  launchArgs: (port) => [`--inspect=127.0.0.1:${port}`],
  launchEnv: () => ({}),
  /** /json/list 中出现 type=node 的主进程 target 即视为可接入 */
  readyTarget: (t) => t.type === 'node' && Boolean(t.webSocketDebuggerUrl),

  /**
   * 执行一次签到检查（内部不重试，重试由 runner 负责）
   * @returns 与 trae 适配器一致的 report 结构
   */
  async sign({ port, dryRun }) {
    const started = Date.now();
    let status = null;
    let claim = null;
    let failure = null;

    try {
      status = await evalOnMain(port, STATUS_SCRIPT);
    } catch (e) {
      failure = `连接客户端失败: ${e.message}`;
    }

    if (!failure && !status?.ok) failure = status?.error || '状态查询失败';

    const checkedInBefore = status?.ok ? extractCheckedIn(status.raw) : null;

    if (!failure && !dryRun && checkedInBefore !== true) {
      let claimResult = null;
      try {
        claim = await evalOnMain(port, CLAIM_SCRIPT);
        if (claim?.ok) claimResult = interpretClaim(claim.raw);
        if (!claim?.ok) failure = claim?.error || '签到调用失败';
        else if (!claimResult.ok) failure = claimResult.error;
      } catch (e) {
        failure = `签到失败: ${e.message}`;
      }

      // 领取后复查：以服务端状态作为最终判定
      if (!failure) {
        try {
          const after = await evalOnMain(port, STATUS_SCRIPT);
          if (after?.ok && extractCheckedIn(after.raw) !== true) {
            failure = '已调用领取，但服务端状态仍显示今天未签到';
          }
          status = after?.ok ? after : status;
        } catch { /* 复查失败不影响已成功的领取结果 */ }
      }
    }

    const checkedInAfter = status?.ok ? extractCheckedIn(status.raw) : null;

    return {
      time: new Date().toISOString(),
      durationMs: Date.now() - started,
      dryRun,
      ok: !failure,
      checkedInBefore,
      claimed: Boolean(claim?.ok && !failure),
      checkedInAfter,
      statusRaw: status?.raw ?? null,
      claimRaw: claim?.raw ?? null,
      error: failure,
    };
  },
};
