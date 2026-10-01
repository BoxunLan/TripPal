import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// 确定性检查：单槽 + 固定 seed 下，同一张卡的多次运行必须给出完全相同的判定与断言得分。
const root = path.join(os.tmpdir(), "v2-deterministic", "runs", "v2det", "B-no-skill");
const byTask = {};
for (const t of fs.readdirSync(root)) {
  for (const n of fs.readdirSync(path.join(root, t))) {
    const d = path.join(root, t, n);
    const vp = path.join(d, "verdict.json");
    if (!fs.existsSync(vp)) continue;
    const v = JSON.parse(fs.readFileSync(vp, "utf8"));
    let score = "-";
    const ap = path.join(d, "artifacts", "answer.json");
    if (fs.existsSync(ap)) {
      const asr = JSON.parse(fs.readFileSync(ap, "utf8")).assertions || [];
      score = `${asr.filter((x) => x.passed).length}/${asr.length}`;
    }
    (byTask[t] = byTask[t] || []).push(`${v.status}(${score})`);
  }
}

let stable = 0;
console.log("task                     3 次运行结果                     一致?");
for (const [t, rs] of Object.entries(byTask).sort()) {
  const same = new Set(rs).size === 1;
  if (same) stable++;
  console.log(`  ${t.padEnd(24)} ${rs.join("  ").padEnd(34)} ${same ? "一致" : "不一致"}`);
}
const total = Object.keys(byTask).length;
console.log("");
console.log(`判定一致的任务：${stable}/${total}（${((100 * stable) / total).toFixed(0)}%）`);
const all = Object.values(byTask).flat();
console.log(`总运行 ${all.length} 次，通过 ${all.filter((x) => x.startsWith("pass")).length} 次`);
