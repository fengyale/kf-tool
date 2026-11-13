// TRAE SOLO CN 自动签到（兼容入口，逻辑已迁移至 apps/trae.mjs）
//
// 用法：
//   node src/sign.mjs            # 查状态，未签到则签到
//   node src/sign.mjs --dry-run  # 只查状态，不签到
//   node src/sign.mjs --json     # 输出机器可读的 JSON

import { traeApp } from './apps/trae.mjs';

const args = new Set(process.argv.slice(2));
const DRY_RUN = args.has('--dry-run');
const AS_JSON = args.has('--json');
const PORT = Number(process.env.TRAE_CDP_PORT || traeApp.defaultPort);

const report = await traeApp.sign({ port: PORT, dryRun: DRY_RUN });

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  if (report.error) {
    console.log(`[失败] ${report.error}`);
  } else if (report.checkedInBefore === true) {
    console.log('[跳过] 今日已签到，无需重复领取');
  } else if (DRY_RUN) {
    console.log('[预演] 尚未签到（未执行领取）');
  } else {
    console.log('[成功] 已完成签到');
  }
  console.log('--- 状态原始返回 ---');
  console.log(JSON.stringify(report.statusRaw, null, 2));
  if (report.claimRaw) {
    console.log('--- 签到原始返回 ---');
    console.log(JSON.stringify(report.claimRaw, null, 2));
  }
}

process.exit(report.error ? 1 : 0);
