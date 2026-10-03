import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// 对比"全量注入"与"按域注入"：注入范围是否如路由表所声明，以及结果层面的差异。
function collect(root) {
  const out = new Map();
  if (!fs.existsSync(root)) return out;
  for (const t of fs.readdirSync(root)) {
    for (const n of fs.readdirSync(path.join(root, t))) {
      const d = path.join(root, t, n);
      const vp = path.join(d, "verdict.json");
      if (!fs.existsSync(vp)) continue;
      const v = JSON.parse(fs.readFileSync(vp, "utf8"));
      const m = path.join(d, "manifest.json");
      const man = fs.existsSync(m) ? JSON.parse(fs.readFileSync(m, "utf8")) : {};
      let a = 0, b = 0;
      const ap = path.join(d, "artifacts", "answer.json");
      if (fs.existsSync(ap)) {
        const asr = JSON.parse(fs.readFileSync(ap, "utf8")).assertions || [];
        a = asr.filter((x) => x.passed).length;
        b = asr.length;
      }
      const snap = man.skill_snapshot || {};
      out.set(t, {
        status: v.status,
        attr: v.attribution,
        kind: (v.failure_kind || (v.scores ? "" : "")) || "",
        a, b,
        files: (snap.files || []).map((f) => f.path),
        routed: snap.routing || null,
      });
    }
  }
  return out;
}

const full = collect(path.join(os.tmpdir(), "v2-route-full", "runs", "routefull", "C-seed-skill"));
const routed = collect(path.join(os.tmpdir(), "v2-route-routed", "runs", "routerouted", "C-seed-skill"));

let n = 0, fa = 0, fb = 0, ra = 0, rb = 0, fPass = 0, rPass = 0;
console.log("task                scenario  注入文件数(全量/按域)  命中的分片                     结果(全量 / 按域)");
for (const [t, F] of [...full].sort()) {
  const R = routed.get(t);
  if (!R) continue;
  n++;
  fa += F.a; fb += F.b; ra += R.a; rb += R.b;
  if (F.status === "pass") fPass++;
  if (R.status === "pass") rPass++;
  const frags = (R.routed ? R.routed.fragments : []).map((p) => p.split("/").pop()).join(",") || "(无)";
  console.log(
    `${t.padEnd(20)}${(R.routed ? R.routed.task_value : "?").padEnd(10)}${String(F.files.length).padStart(8)} / ${String(R.files.length).padEnd(8)}  ${frags.padEnd(30)}${F.status}(${F.a}/${F.b}) / ${R.status}(${R.a}/${R.b})`
  );
}
console.log("");
console.log(`配对 ${n} 张`);
console.log(`整卡通过：全量 ${fPass}/${n}   按域 ${rPass}/${n}`);
console.log(`断言通过：全量 ${fa}/${fb} = ${((100 * fa) / Math.max(1, fb)).toFixed(1)}%   按域 ${ra}/${rb} = ${((100 * ra) / Math.max(1, rb)).toFixed(1)}%`);
const fullCounts = new Set([...full.values()].map((v) => v.files.length));
const routedCounts = [...routed.values()].map((v) => v.files.length);
console.log(`注入文件数：全量恒为 ${[...fullCounts].join("/")}；按域在 ${Math.min(...routedCounts)}–${Math.max(...routedCounts)} 之间`);
