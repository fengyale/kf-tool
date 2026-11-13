// 每日签到入口（支持多客户端）
//
// 用法：
//   node src/run-daily.mjs                  # 签到 config.json 中已配置的全部客户端
//   node src/run-daily.mjs trae             # 只签 TRAE
//   node src/run-daily.mjs workbuddy        # 只签 WorkBuddy
//   node src/run-daily.mjs all              # 两个都签（按顺序，互不影响）
//   node src/run-daily.mjs trae --dry-run   # 只看状态不签到
//   node src/run-daily.mjs --no-launch      # 客户端可直接接入时才签，否则跳过
//   node src/run-daily.mjs trae --exe "X:\path\app.exe"   # 临时指定单个客户端路径

import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { runForApp } from './runner.mjs';
import { traeApp } from './apps/trae.mjs';
import { workbuddyApp } from './apps/workbuddy.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');

const APPS = new Map([
  [traeApp.id, traeApp],
  [workbuddyApp.id, workbuddyApp],
]);

// ---------- 参数解析 ----------
const argv = process.argv.slice(2);
const flags = new Set();
const positionals = [];
let exeOverride = null;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--exe') { exeOverride = argv[++i] || null; continue; }
  if (a.startsWith('--')) flags.add(a);
  else positionals.push(a);
}
const DRY_RUN = flags.has('--dry-run');
const NO_LAUNCH = flags.has('--no-launch');

// ---------- 配置读取（兼容旧版 {exe, port} 结构，自动视为 trae） ----------
function readConfig() {
  const cfgPath = join(ROOT, 'config.json');
  let cfg = {};
  if (existsSync(cfgPath)) {
    try { cfg = JSON.parse(readFileSync(cfgPath, 'utf8')); } catch { /* 损坏则忽略 */ }
  }
  if (cfg.apps) return cfg.apps;
  if (cfg.exe) return { trae: { exe: cfg.exe, port: cfg.port } };
  return {};
}
const CONFIGURED = readConfig();

// ---------- 决定本次要跑哪些客户端 ----------
const ENV_OVERRIDES = {
  trae: { exe: process.env.TRAE_EXE || null, port: Number(process.env.TRAE_CDP_PORT || 0) || null },
  workbuddy: { exe: process.env.WORKBUDDY_EXE || null, port: Number(process.env.WORKBUDDY_CDP_PORT || 0) || null },
};

let selected = positionals.length ? positionals : ['__configured__'];
if (selected.length === 1 && selected[0] === 'all') {
  selected = [...APPS.keys()];
} else if (selected[0] === '__configured__') {
  // 默认：已配置的客户端；一个都没配过则退回 trae（保持旧行为）
  selected = Object.keys(CONFIGURED).filter((id) => APPS.has(id));
  if (selected.length === 0) selected = ['trae'];
}

for (const id of selected) {
  if (!APPS.has(id)) {
    console.error(`未知客户端: ${id}（支持: ${[...APPS.keys()].join(', ')}, all）`);
    process.exit(1);
  }
}
if (exeOverride && selected.length !== 1) {
  console.error('--exe 只能在指定单个客户端时使用');
  process.exit(1);
}

// ---------- 顺序执行，相互隔离 ----------
const codes = [];
for (const id of selected) {
  const app = APPS.get(id);
  const envOv = ENV_OVERRIDES[id] || {};
  const cfg = CONFIGURED[id] || {};
  const code = await runForApp(app, {
    root: ROOT,
    exe: exeOverride || envOv.exe || cfg.exe || null,
    port: envOv.port || cfg.port || app.defaultPort,
    dryRun: DRY_RUN,
    noLaunch: NO_LAUNCH,
  });
  codes.push([id, code]);
}

// 任一失败则整体失败（跳过码 2 不算失败）
const failed = codes.some(([, c]) => c === 1);
process.exit(failed ? 1 : 0);
