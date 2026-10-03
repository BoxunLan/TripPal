// 汇总本端小模型跑批的结果，产出一份轻量分析（不复制原始轨迹）。
//
// 用法（skill-loop 目录下）：
//   node integration/pipeline/analyze_local_track.mjs <runRoot> <packPath> <outPath>
// 例：
//   node integration/pipeline/analyze_local_track.mjs \
//     "$env:TEMP/v2-local-skill/runs/localskill/C-seed-skill" \
//     modules/demand-task-factory/out-v2/task_pack.jsonl \
//     ../docs/local-track-analysis.md
//
// 为什么单独写：原始 trace/verdict 体积大、不适合入库，但"哪些模式全灭、失败卡在哪些断言上、
// 决策混淆成什么样"这些**聚合结论**才是三端合看时真正需要的。

import fs from "node:fs";
import path from "node:path";

const [runRoot, packPath, outPath] = process.argv.slice(2);
if (!runRoot || !packPath || !outPath) {
  console.error("用法: node analyze_local_track.mjs <runRoot> <packPath> <outPath>");
  process.exit(2);
}

const pack = new Map(
  fs.readFileSync(packPath, "utf8").split(/\r?\n/).filter(Boolean)
    .map((l) => JSON.parse(l)).map((c) => [c.task_id, c])
);

const patternOf = (id) => id.replace(/-\d+$/, "");
const trunc = (s, n = 70) => {
  const t = String(s ?? "").replace(/\s+/g, " ");
  return t.length > n ? t.slice(0, n) + "…" : t;
};
const rate = (a, b) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");

const rows = [];
for (const taskId of fs.readdirSync(runRoot)) {
  const taskDir = path.join(runRoot, taskId);
  if (!fs.statSync(taskDir).isDirectory()) continue;
  for (const runN of fs.readdirSync(taskDir)) {
    const d = path.join(taskDir, runN);
    const vp = path.join(d, "verdict.json");
    if (!fs.existsSync(vp)) continue;
    const verdict = JSON.parse(fs.readFileSync(vp, "utf8"));
    let assertions = [], parsed = null, text = "";
    const ap = path.join(d, "artifacts", "answer.json");
    if (fs.existsSync(ap)) {
      const a = JSON.parse(fs.readFileSync(ap, "utf8"));
      assertions = a.assertions || [];
      parsed = a.parsed ?? null;
      text = a.text || "";
    }
    const card = pack.get(taskId) || {};
    rows.push({
      taskId,
      family: taskId[2],
      pattern: patternOf(taskId),
      scenario: card.scenario_id || "?",
      expected: card.expected_end_state || {},
      status: verdict.status,
      pass: verdict.status === "pass",
      attribution: verdict.attribution,
      failureKind: verdict.failure_kind,
      passed: assertions.filter((x) => x.passed).length,
      total: assertions.length,
      failed: assertions.filter((x) => !x.passed),
      parsed,
      text,
    });
    break; // Runs=1；多 run 时取第一个，避免重复计数
  }
}

const sum = (xs, f) => xs.reduce((s, x) => s + f(x), 0);
const groupBy = (xs, f) => {
  const m = new Map();
  for (const x of xs) {
    const k = f(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return m;
};

const n = rows.length;
const pass = rows.filter((r) => r.pass).length;
const aTot = sum(rows, (r) => r.total);
const aPass = sum(rows, (r) => r.passed);
const kinds = groupBy(rows, (r) => r.failureKind || "-");
const holdout = rows.filter((r) => (pack.get(r.taskId) || {}).split === "holdout");
const train = rows.filter((r) => (pack.get(r.taskId) || {}).split === "train");

const L = [];
L.push("# 本端小模型轨 —— 结果分析（轻量汇总）");
L.push("");
L.push("> 本文件由 `skill-loop/integration/pipeline/analyze_local_track.mjs` 自动汇总生成，**不含原始轨迹**。");
L.push("> 运行身份与冻结基线见 [`local-track-trajectory.md`](local-track-trajectory.md)；原始 `verdict/trace` 未入库。");
L.push("");
L.push("## 1. 总览");
L.push("");
L.push("| 指标 | 值 |");
L.push("|---|---|");
L.push(`| 任务数 | ${n} |`);
L.push(`| 整卡通过 | **${pass} / ${n} = ${rate(pass, n)}** |`);
L.push(`| 断言通过 | **${aPass} / ${aTot} = ${rate(aPass, aTot)}** |`);
L.push(`| train / holdout | ${rate(train.filter((r) => r.pass).length, train.length)} / ${rate(holdout.filter((r) => r.pass).length, holdout.length)} |`);
L.push(`| 完成状态 | 通过 ${pass} · 内容失败 ${rows.filter((r) => r.status === "fail").length} · 执行错误 ${rows.filter((r) => r.status === "error").length} |`);
L.push("");
L.push("> 断言通过率的分母是**实际产出断言的卡**：" + `${rows.filter((r) => r.total > 0).length} / ${n} 张。`);
L.push("> 执行错误（预算截断）没有断言可算，不进入分母，因此这类卡的失败不会拉低断言率——");
L.push("> 看按模式表时请注意" + "「通过 6/7 但断言 100%」" + "这种组合通常意味着第 7 张是执行错误。");
L.push("");
L.push("归因分布：");
L.push("");
L.push("| failure_kind | 张数 |");
L.push("|---|---|");
for (const [k, v] of [...kinds].sort((a, b) => b[1].length - a[1].length)) L.push(`| \`${k}\` | ${v.length} |`);
L.push("");

L.push("## 2. 按族");
L.push("");
L.push("| 族 | 张数 | 整卡通过 | 断言通过率 | 执行错误 | 最常见的未命中断言 |");
L.push("|---|---|---|---|---|---|");
for (const [fam, rs] of [...groupBy(rows, (r) => r.family)].sort()) {
  const top = groupBy(rs.flatMap((r) => r.failed), (f) => f.op);
  const topOp = [...top].sort((a, b) => b[1].length - a[1].length)[0];
  L.push(
    `| ${fam} | ${rs.length} | ${rs.filter((r) => r.pass).length} (${rate(rs.filter((r) => r.pass).length, rs.length)}) | ` +
    `${rate(sum(rs, (r) => r.passed), sum(rs, (r) => r.total))} | ${rs.filter((r) => r.status === "error").length} | ` +
    `${topOp ? `\`${topOp[0]}\` ×${topOp[1].length}` : "—"} |`
  );
}
L.push("");

L.push("## 3. 按模式（`task_id` 去掉序号）");
L.push("");
L.push("| 模式 | 张数 | 整卡通过 | 断言通过率 | 主要失败断言 |");
L.push("|---|---|---|---|---|");
for (const [pat, rs] of [...groupBy(rows, (r) => r.pattern)].sort()) {
  const top = [...groupBy(rs.flatMap((r) => r.failed), (f) => f.op)].sort((a, b) => b[1].length - a[1].length)[0];
  L.push(
    `| \`${pat}\` | ${rs.length} | ${rs.filter((r) => r.pass).length}/${rs.length} | ` +
    `${rate(sum(rs, (r) => r.passed), sum(rs, (r) => r.total))} | ${top ? `\`${top[0]}\` ×${top[1].length}` : "—"} |`
  );
}
L.push("");

L.push("## 4. 失败断言画像");
L.push("");
const failedAll = rows.flatMap((r) => r.failed);
L.push("| 算子 | 失败次数 | 占全部断言 |");
L.push("|---|---|---|");
for (const [op, fs_] of [...groupBy(failedAll, (f) => f.op)].sort((a, b) => b[1].length - a[1].length)) {
  L.push(`| \`${op}\` | ${fs_.length} | ${rate(fs_.length, aTot)} |`);
}
L.push("");
L.push("**最常见的未命中 `contains`（逐字引用类）**");
L.push("");
L.push("| 期望片段 | 未命中张数 |");
L.push("|---|---|");
for (const [val, fs_] of [...groupBy(failedAll.filter((f) => f.op === "contains"), (f) => String(f.value))].sort((a, b) => b[1].length - a[1].length).slice(0, 10)) {
  L.push(`| \`${trunc(val)}\` | ${fs_.length} |`);
}
L.push("");
L.push("**最常见的未命中 `regex`（条款/格式类）**");
L.push("");
L.push("| 期望正则 | 未命中张数 |");
L.push("|---|---|");
for (const [val, fs_] of [...groupBy(failedAll.filter((f) => f.op === "regex"), (f) => String(f.value))].sort((a, b) => b[1].length - a[1].length).slice(0, 10)) {
  L.push(`| \`${trunc(val)}\` | ${fs_.length} |`);
}
L.push("");

// §4b 由数字自动判定的要点（不写主观结论，只把上面几张表里最该被看见的行挑出来）
const budget = rows.filter((r) => r.failureKind === "budget_exhausted");
const opFail = [...groupBy(failedAll, (f) => f.op)].sort((a, b) => b[1].length - a[1].length);
const lenFloor = failedAll.filter((f) => f.op === "regex" && String(f.value).includes("{400,}"));
const wrongDecision = rows.filter((r) => r.expected.decision && r.parsed?.decision && r.parsed.decision !== r.expected.decision);
const unparsed = rows.filter((r) => r.expected.decision && !r.parsed?.decision);
L.push("## 4b. 要点（由上面的数字自动挑出，供三端合看时快速定位）");
L.push("");
L.push(`1. **失败断言以 \`regex\`（覆盖/格式类）为主**：${opFail[0] ? `\`${opFail[0][0]}\` ${opFail[0][1].length} 次` : "—"}` +
  `，而 \`contains\`（逐字引用类）只有 ${failedAll.filter((f) => f.op === "contains").length} 次` +
  ` —— 在这一配置下，小模型的瓶颈已不是"引用来源"，而是"把要求的条目/格式写全"。`);
L.push(`2. **${lenFloor.length} 张只差篇幅**：未命中的正则是 \`(?s).{400,}\`（C 族清单长度下限），` +
  `属于输出啰嗦度/预算问题，不是知识缺口。`);
L.push(`3. **决策类错误 ${wrongDecision.length} 张、另有 ${unparsed.length} 张没解析出决策**：` +
  `明细见 §5 的"← 错"行（\`not_eligible\` 与 \`eligible\` 互错、以及 \`use_visa\` 被答成 \`not_eligible\`）。`);
L.push(`4. **${budget.length} 张 \`budget_exhausted\` 会低估上面的比例**：它们没产出正文，` +
  `因此既不算通过、也没有断言进入分母。`);
L.push("");

const decisionCards = rows.filter((r) => r.expected.decision);if (decisionCards.length) {
  L.push("## 5. 决策类混淆（规则应用族）");
  L.push("");
  L.push("| 期望决策 | 模型实际给出 | 张数 |");
  L.push("|---|---|---|");
  const conf = groupBy(decisionCards, (r) => `${r.expected.decision} → ${r.parsed?.decision ?? "(未解析出)"}`);
  for (const [k, rs] of [...conf].sort((a, b) => b[1].length - a[1].length)) {
    const [exp, got] = k.split(" → ");
    const mark = exp === got ? "" : " ← 错";
    L.push(`| \`${exp}\` | \`${got}\`${mark} | ${rs.length} |`);
  }
  L.push("");
}

const actionCards = rows.filter((r) => r.expected.action);
if (actionCards.length) {
  L.push("## 5b. 动作类混淆（到达后动作族）");
  L.push("");
  L.push("| 期望动作 | 模型实际给出 | 张数 |");
  L.push("|---|---|---|");
  for (const [k, rs] of [...groupBy(actionCards, (r) => `${r.expected.action} → ${r.parsed?.action ?? "(未解析出)"}`)].sort((a, b) => b[1].length - a[1].length)) {
    const [exp, got] = k.split(" → ");
    L.push(`| \`${exp}\` | \`${got}\`${exp === got ? "" : " ← 错"} | ${rs.length} |`);
  }
  L.push("");
}

L.push("## 6. 执行错误清单（与模型内容判断无关）");
L.push("");
if (budget.length) {
  L.push(`共 **${budget.length}** 张 ` + "`budget_exhausted`" + `（推理 token 吃满、未产出正文）：`);
  L.push("");
  L.push("```");
  L.push(budget.map((r) => r.taskId).sort().join(", "));
  L.push("```");
} else {
  L.push("无。");
}
L.push("");

const toolCards = rows.filter((r) => typeof r.expected.use_tool === "boolean");
if (toolCards.length) {
  L.push("## 7. 工具决策");
  L.push("");
  L.push("| 期望 use_tool | 模型实际 | 张数 |");
  L.push("|---|---|---|");
  for (const [k, rs] of [...groupBy(toolCards, (r) => `${r.expected.use_tool} → ${r.parsed?.use_tool ?? "(未解析)"}`)].sort((a, b) => b[1].length - a[1].length)) {
    const [exp, got] = k.split(" → ");
    L.push(`| \`${exp}\` | \`${got}\`${exp === got ? "" : " ← 错"} | ${rs.length} |`);
  }
  L.push("");
}

L.push("## 8. 读这份分析时的限制");
L.push("");
L.push("- **没有对照臂**：本端只跑了 `C-seed-skill`（按域注入），没跑同包同配置的 `B-no-skill`。");
L.push("  因此上面的数字**不能**拆成「skill 的贡献」与「题目变简单的贡献」。");
L.push("- **只在这一配置下成立**：MiniCPM5 2.6B / 64K 上下文 / 4 并发 / 按域注入 / 4096 输出预算。");
L.push("- **原始轨迹未入库**（体积原因）。需要逐卡证据时按 `local-track-trajectory.md` 的复现命令重跑，");
L.push("  或用该文件里记录的产物路径读取当次运行的 `verdict.json` / `trace.jsonl` / `artifacts/answer.json`。");
L.push("");

fs.writeFileSync(outPath, L.join("\n") + "\n", { encoding: "utf8" });
console.log(`[analyze] 写入 ${outPath}`);
console.log(`[analyze] 任务 ${n}｜整卡通过 ${pass} (${rate(pass, n)})｜断言 ${aPass}/${aTot} (${rate(aPass, aTot)})｜模式 ${new Set(rows.map((r) => r.pattern)).size} 个`);
