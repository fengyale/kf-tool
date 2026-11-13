// TRAE Work 适配器
// 兼容两代客户端：
//  - 新版（Trae CN）：渲染层经 window.vscode.ipcRenderer 直调主进程通道
//    vscode:sandbox::main-invoke-fetchCheckinCreditsStatus / -claimCheckinCredits
//    （与 UI 服务 fetchCheckinCreditsStatus()/claimCheckinCredits() 同一条链路，鉴权由客户端处理）
//  - 旧版（TRAE SOLO CN）：CDP 取 __SOLO_LITE_PORT_REGISTRY__ 中的 CheckinCreditsPort 实例调 claim()/fetchStatus()
// 优先新版桥；不存在时自动回退旧注册表。

import { findTargets, withTarget } from '../cdp.mjs';

/** 查询签到状态（新版桥优先，旧注册表回退） */
const STATUS_SCRIPT = `
(async () => {
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v)); } catch { return String(v); } };

  // —— 新版：window.vscode 桥 ——
  try {
    const ipc = globalThis.vscode && globalThis.vscode.ipcRenderer;
    if (ipc && typeof ipc.invoke === 'function') {
      const r = await ipc.invoke('vscode:sandbox::main-invoke-fetchCheckinCreditsStatus');
      const d = r && r.data;
      if (r && r.status === 200 && d && typeof d.enable === 'boolean' && typeof d.checked_in === 'boolean') {
        return JSON.stringify({ ok: true, via: 'vscode-bridge', raw: safe(r) });
      }
      return JSON.stringify({ ok: false, via: 'vscode-bridge', error: '签到状态响应结构异常', raw: safe(r) });
    }
  } catch (e) { /* 桥不可用则落回旧版 */ }

  // —— 旧版：端口注册表 ——
  const R = globalThis.__SOLO_LITE_PORT_REGISTRY__;
  if (!R || !R.instances) {
    const diag = { hasVscodeBridge: !!(globalThis.vscode && globalThis.vscode.ipcRenderer), hasRegistry: !!R };
    return JSON.stringify({ ok: false, error: '未找到 window.vscode 桥或 __SOLO_LITE_PORT_REGISTRY__（客户端版本可能已变更）', diag });
  }

  let port = null;
  for (const [k, v] of R.instances.entries()) {
    if (String(k).includes('CheckinCredits')) { port = v; break; }
  }
  if (!port) {
    const known = Array.from(R.instances.keys()).map(String).filter(s => /Checkin|Credit/i.test(s));
    return JSON.stringify({ ok: false, error: '未找到 CheckinCreditsPort 实例', knownPorts: known });
  }
  if (typeof port.fetchStatus !== 'function') {
    return JSON.stringify({ ok: false, error: 'CheckinCreditsPort 上没有 fetchStatus 方法' });
  }

  const raw = await port.fetchStatus();
  return JSON.stringify({ ok: true, via: 'port-registry', raw: safe(raw) });
})()
`;

/** 执行签到（新版桥优先，旧注册表回退） */
const CLAIM_SCRIPT = `
(async () => {
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v)); } catch { return String(v); } };

  // —— 新版：window.vscode 桥 ——
  try {
    const ipc = globalThis.vscode && globalThis.vscode.ipcRenderer;
    if (ipc && typeof ipc.invoke === 'function') {
      const r = await ipc.invoke('vscode:sandbox::main-invoke-claimCheckinCredits');
      const d = r && r.data;
      const code = d && typeof d.code === 'number' ? d.code : undefined;
      if (r && r.status === 200 && (code === undefined || code === 0)) {
        return JSON.stringify({ ok: true, via: 'vscode-bridge', raw: safe(r) });
      }
      const msg = d && typeof d.message === 'string' ? d.message : '';
      return JSON.stringify({ ok: false, via: 'vscode-bridge', error: ('签到业务失败 code=' + code + (msg ? '：' + msg : '')).trim(), raw: safe(r) });
    }
  } catch (e) { /* 桥不可用则落回旧版 */ }

  // —— 旧版：端口注册表 ——
  const R = globalThis.__SOLO_LITE_PORT_REGISTRY__;
  let port = null;
  if (R && R.instances) {
    for (const [k, v] of R.instances.entries()) {
      if (String(k).includes('CheckinCredits')) { port = v; break; }
    }
  }
  if (!port) return JSON.stringify({ ok: false, error: '未找到 window.vscode 桥或 CheckinCreditsPort 实例' });
  if (typeof port.claim !== 'function') return JSON.stringify({ ok: false, error: 'CheckinCreditsPort 上没有 claim 方法' });

  const raw = await port.claim();
  return JSON.stringify({ ok: true, via: 'port-registry', raw: safe(raw) });
})()
`;

/** 从客户端返回结构里尽力提取“今天是否已签到” */
function extractCheckedIn(raw) {
  const d = raw?.data ?? raw?.Data ?? raw;
  if (d == null || typeof d !== 'object') return null;
  for (const k of ['checked_in', 'checkedIn', 'is_checked_in', 'today_checked_in']) {
    if (typeof d[k] === 'boolean') return d[k];
    if (typeof d[k] === 'number') return d[k] === 1;
  }
  return null;
}

/**
 * 探测某个页面是否具备签到能力：
 *  - 新版桥 globalThis.vscode.ipcRenderer.invoke
 *  - 旧版注册表 __SOLO_LITE_PORT_REGISTRY__ 里的 CheckinCreditsPort
 */
const PROBE_SCRIPT = `
(() => {
  try {
    const ipc = globalThis.vscode && globalThis.vscode.ipcRenderer;
    if (ipc && typeof ipc.invoke === 'function') return true;
  } catch {}
  try {
    const R = globalThis.__SOLO_LITE_PORT_REGISTRY__;
    if (R && R.instances) {
      for (const [k, v] of R.instances.entries()) {
        if (String(k).includes('CheckinCredits') && v && typeof v.fetchStatus === 'function') return true;
      }
    }
  } catch {}
  return false;
})()
`;

/** 候选 target 排序：solo-lite（Agent 窗口，新旧能力俱全）> workbench > 其他；跳过悬浮窗 */
function rankTargets(pages) {
  const score = (t) => {
    const url = String(t.url || '');
    if (/solo-lite\.html/i.test(url)) return 3;
    if (/workbench\.html/i.test(url)) return 2;
    if (/floating|iCubeFloating/i.test(url)) return 0;
    return 1;
  };
  return [...pages].sort((a, b) => score(b) - score(a));
}

/**
 * 选出一个真正具备签到能力的 target。
 * 注意：新版客户端（3.4.x）把 __SOLO_LITE_PORT_REGISTRY__ 放在 solo-lite.html（Agent 窗口），
 * 而 workbench.html 只有 vscode 桥；冷启动初期桥可能尚未注入。
 * 因此必须逐个候选探测，不能固定用第一个 workbench 页面。
 */
async function pickTarget(port) {
  const pages = await findTargets(port, (t) => t.type === 'page');
  if (pages.length === 0) {
    throw new Error('未找到 type=page 的 target，客户端可能未启动或未开调试端口');
  }
  const candidates = rankTargets(pages);
  const seen = [];
  for (const t of candidates) {
    const url = String(t.url || '');
    if (/floating|iCubeFloating/i.test(url)) continue;
    try {
      const hit = await withTarget(t, async (session) => {
        await session.send('Runtime.enable');
        return session.evaluate(PROBE_SCRIPT);
      });
      seen.push(`${String(t.title || '').slice(0, 24)}:${hit ? 'OK' : '无能力'}`);
      if (hit) return t;
    } catch (e) {
      seen.push(`${String(t.title || '').slice(0, 24)}:ERR ${e.message.slice(0, 60)}`);
    }
  }
  throw new Error(`没有具备签到能力的页面（探测结果: ${seen.join(' | ') || '无候选'}）`);
}

/** 在指定 target 上执行脚本并解析 JSON 结果 */
async function evalOn(target, expression) {
  const raw = await withTarget(target, async (session) => {
    await session.send('Runtime.enable');
    return session.evaluate(expression);
  });
  return JSON.parse(raw);
}

export const traeApp = {
  id: 'trae',
  tag: 'TRAE',
  displayName: 'TRAE Work',
  defaultPort: 9222,
  defaultImage: 'Trae CN.exe',
  /** 拉起客户端的命令行参数 */
  launchArgs: (port) => [`--remote-debugging-port=${port}`],
  /** 拉起客户端需要追加的环境变量（TRAE 不需要） */
  launchEnv: () => ({}),
  /** /json/list 中出现何种 target 视为可接入 */
  readyTarget: (t) => t.type === 'page',

  /**
   * 执行一次签到检查（内部不重试，重试由 runner 负责）
   * @returns 与旧 sign.mjs 完全一致的 report 结构
   */
  async sign({ port, dryRun }) {
    const started = Date.now();
    let status = null;
    let claim = null;
    let failure = null;
    let target = null;

    try {
      target = await pickTarget(port);
      status = await evalOn(target, STATUS_SCRIPT);
    } catch (e) {
      failure = `连接客户端失败: ${e.message}`;
    }

    if (!failure && !status?.ok) failure = status?.error || '状态查询失败';

    const checkedInBefore = status?.ok ? extractCheckedIn(status.raw) : null;

    if (!failure && !dryRun && checkedInBefore !== true) {
      try {
        claim = await evalOn(target, CLAIM_SCRIPT);
        if (!claim?.ok) failure = claim?.error || '签到调用失败';
      } catch (e) {
        failure = `签到失败: ${e.message}`;
      }
    }

    // 签到后再查一次状态确认
    let statusAfter = null;
    if (!failure && claim) {
      try {
        statusAfter = await evalOn(target, STATUS_SCRIPT);
      } catch { /* 确认失败不影响主流程 */ }
    }

    return {
      time: new Date().toISOString(),
      durationMs: Date.now() - started,
      dryRun,
      ok: !failure,
      checkedInBefore,
      claimed: Boolean(claim),
      checkedInAfter: statusAfter?.ok ? extractCheckedIn(statusAfter.raw) : null,
      statusRaw: status?.raw ?? null,
      claimRaw: claim?.raw ?? null,
      error: failure,
    };
  },
};
