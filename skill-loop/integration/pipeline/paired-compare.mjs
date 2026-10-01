import fs from "node:fs";
import os from "node:os";
import path from "node:path";

function collect(root) {
  const m = new Map();
  if (!fs.existsSync(root)) return m;
  for (const t of fs.readdirSync(root)) {
    const d = path.join(root, t, "1");
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
    m.set(t, { pass: v.status === "pass", a, b, attr: v.attribution });
  }
  return m;
}

const base = collect(path.join(os.tmpdir(), "v2-final329", "runs", "v2final329", "B-no-skill"));
const cand = collect(path.join(os.tmpdir(), "v2-candidate", "runs", "v2candidate", "C-seed-skill"));
const common = [...cand.keys()].filter((k) => base.has(k)).sort();

// 按 (族, 模式) 聚合：模式 = task_id 去掉末尾序号
const pat = (id) => id.replace(/-\d+$/, "");
const g = {};
for (const k of common) {
  const key = pat(k);
  const o = (g[key] = g[key] || { n: 0, bp: 0, cp: 0, ba: 0, bb: 0, ca: 0, cb: 0 });
  const B = base.get(k), C = cand.get(k);
  o.n++;
  if (B.pass) o.bp++;
  if (C.pass) o.cp++;
  o.ba += B.a; o.bb += B.b; o.ca += C.a; o.cb += C.b;
}

console.log(`配对任务 ${common.length} 张（候选已完成 ${cand.size}）`);
console.log("");
console.log("模式                                    张数  基线通过  候选通过   基线断言率  候选断言率");
for (const [k, v] of Object.entries(g).sort()) {
  const br = v.bb ? ((100 * v.ba) / v.bb).toFixed(0) : "-";
  const cr = v.cb ? ((100 * v.ca) / v.cb).toFixed(0) : "-";
  console.log(
    `${k.padEnd(38)} ${String(v.n).padStart(3)}   ${String(v.bp).padStart(3)}/${String(v.n).padEnd(3)}  ${String(v.cp).padStart(3)}/${String(v.n).padEnd(3)}    ${String(br).padStart(4)}%      ${String(cr).padStart(4)}%`
  );
}
