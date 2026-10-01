import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const root = path.join(os.tmpdir(), "v2-deterministic", "runs", "v2det", "B-no-skill");
const byTask = {};
for (const t of fs.readdirSync(root)) {
  for (const n of fs.readdirSync(path.join(root, t))) {
    const d = path.join(root, t, n);
    const vp = path.join(d, "verdict.json");
    if (!fs.existsSync(vp)) continue;
    const v = JSON.parse(fs.readFileSync(vp, "utf8"));
    let a = 0, b = 0;
    const ap = path.join(d, "artifacts", "answer.json");
    if (fs.existsSync(ap)) {
      const asr = JSON.parse(fs.readFileSync(ap, "utf8")).assertions || [];
      a = asr.filter((x) => x.passed).length;
      b = asr.length;
    }
    (byTask[t] = byTask[t] || []).push({ pass: v.status === "pass", rate: b ? a / b : null, a, b });
  }
}

let statusFlip = 0, rateMoves = [], firstDiffers = 0, n = 0;
for (const [t, rs] of Object.entries(byTask)) {
  n++;
  const passes = new Set(rs.map((r) => r.pass));
  if (passes.size > 1) statusFlip++;
  const rates = rs.map((r) => r.rate).filter((x) => x !== null);
  if (rates.length > 1) {
    const spread = Math.max(...rates) - Math.min(...rates);
    rateMoves.push(spread);
    if (spread > 0.001 && Math.abs(rates[1] - rates[2]) < 1e-9) firstDiffers++;
  }
}
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
console.log(`任务数 ${n}，每卡 3 次重复（Jobs=1, LLM_SEED=0, temperature=0）`);
console.log(`状态翻转的任务：${statusFlip}/${n} = ${((100 * statusFlip) / n).toFixed(0)}%`);
console.log(`断言通过率的极差（同一卡 3 次之间）：均值 ${mean(rateMoves).toFixed(3)}，最大 ${Math.max(...rateMoves).toFixed(3)}`);
console.log(`不一致且"仅第 1 次不同（第 2、3 次相同）"的任务：${firstDiffers}/${n}`);
