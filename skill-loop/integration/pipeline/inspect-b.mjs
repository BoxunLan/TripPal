import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const cards = new Map(
  fs.readFileSync("modules/demand-task-factory/out-v2/task_pack.jsonl", "utf8")
    .split(/\r?\n/).filter(Boolean).map((l) => [JSON.parse(l).task_id, JSON.parse(l)])
);

function show(label, root, family, limit) {
  console.log(label);
  if (!fs.existsSync(root)) { console.log("  (no dir)"); return; }
  let n = 0;
  for (const t of fs.readdirSync(root).sort()) {
    if (t[2] !== family) continue;
    const d = path.join(root, t, "1");
    const vp = path.join(d, "verdict.json");
    const ap = path.join(d, "artifacts", "answer.json");
    if (!fs.existsSync(vp) || !fs.existsSync(ap)) continue;
    const a = JSON.parse(fs.readFileSync(ap, "utf8"));
    const v = JSON.parse(fs.readFileSync(vp, "utf8"));
    const card = cards.get(t);
    console.log(`  ${t} 期望=${card.expected_end_state.decision} 解析=${JSON.stringify(a.parsed && a.parsed.decision)} 判定=${v.status}`);
    console.log(`     答: ${String(a.text || "").replace(/\s+/g, " ").slice(0, 230)}`);
    if (++n >= limit) break;
  }
}

show("=== 候选 SKILL 的 B 族作答 ===", path.join(os.tmpdir(), "v2-candidate", "runs", "v2candidate", "C-seed-skill"), "B", 3);
show("=== 基线的 B 族作答（对照）===", path.join(os.tmpdir(), "v2-final329", "runs", "v2final329", "B-no-skill"), "B", 2);
