// 极简 CDP 客户端（TRAE / WorkBuddy 通用）
// 依赖 Node 22+ 内置 WebSocket / fetch，无需任何第三方包
// 端口由调用方传入，不再从环境变量固定读取

const HOST = '127.0.0.1';

export async function listTargets(port) {
  const res = await fetch(`http://${HOST}:${port}/json/list`);
  if (!res.ok) throw new Error(`列举 targets 失败: HTTP ${res.status}`);
  return res.json();
}

/** 列出满足条件的 target（type / url 由 predicate 决定） */
export async function findTargets(port, predicate) {
  const list = await listTargets(port);
  return list.filter(predicate);
}

export class CdpSession {
  constructor(ws) {
    this.ws = ws;
    this._id = 0;
    this._pending = new Map();
    this.onEvent = null;
  }

  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', () => reject(new Error(`WebSocket 连接失败: ${wsUrl}`)), { once: true });
    });

    const session = new CdpSession(ws);
    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.id && session._pending.has(msg.id)) {
        const { resolve, reject } = session._pending.get(msg.id);
        session._pending.delete(msg.id);
        if (msg.error) reject(new Error(`CDP ${msg.method}: ${JSON.stringify(msg.error)}`));
        else resolve(msg.result);
      } else if (msg.method) {
        session.onEvent?.(msg);
      }
    });
    return session;
  }

  send(method, params = {}) {
    const id = ++this._id;
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /** 在页面上下文求值；支持 await 与按值返回 */
  async evaluate(expression, { awaitPromise = true, returnByValue = true } = {}) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise,
      returnByValue,
      userGesture: true,
    });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails.exception?.description
        || r.exceptionDetails.text
        || '未知异常';
      throw new Error(`页面求值异常: ${d}`);
    }
    return r.result.value;
  }

  close() {
    try { this.ws.close(); } catch { /* 忽略 */ }
  }
}

/** 连上指定 target，把 session 交给回调，结束后自动关闭 */
export async function withTarget(target, fn) {
  const session = await CdpSession.connect(target.webSocketDebuggerUrl);
  try {
    return await fn(session, target);
  } finally {
    session.close();
  }
}

/**
 * 在候选 target 上逐个执行探针表达式，返回第一个探针结果为真的 target。
 * 用于业务代码可能位于多个 page / iframe target 中的场景。
 */
export async function probeTarget(port, predicate, probeExpression) {
  const targets = await findTargets(port, predicate);
  let lastErr = '';
  for (const t of targets) {
    try {
      const hit = await withTarget(t, async (session) => {
        await session.send('Runtime.enable');
        return session.evaluate(probeExpression);
      });
      if (hit) return t;
    } catch (e) {
      lastErr = e.message;
    }
  }
  throw new Error(
    `没有探针命中的 target（候选 ${targets.length} 个）`
    + (lastErr ? `；最后错误: ${lastErr.slice(0, 200)}` : ''),
  );
}

export async function withProbedTarget(port, predicate, probeExpression, fn) {
  const target = await probeTarget(port, predicate, probeExpression);
  return withTarget(target, fn);
}
