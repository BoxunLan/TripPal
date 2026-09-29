# blocker: e7-trippal-baseline-pass-rate-1.0

## 现状（已更新：末次实测已不触发 E7）

末次真模型实测（变体 `trippal`，日本线已移除后、`require_china_context` 已生效）：

```
[2] B 跑 live-baseline（B-no-skill）
    ✓ pass_at_3=0.958333 train=0.952381 holdout=1.0 avg_tokens=655.771
[3] 复制 attribution=knowledge_gap 且 split=train 的运行 → 复制 2 次
[4] C 对临时目录 propose → proposal_state=proposed 提案 1 条
[6] C 回归门 → conclusion=pass
```

即基线不再是 1.0，出现了 2 条 `knowledge_gap`（来源断言未命中的 `TP-S01-fact` / `TP-S03-fact`），
C 学出一条真实提案 `ch-001`（「来源与生效日期检查」），候选侧 holdout 未回退（1.0）、
成本门过（`total` 比值 1.366 ≤ 1.5）。**本 blocker 当前不成立**，保留为历史记录。

## 历史现象（曾经触发）

在移除日本线之前，TripPal 曾单独作为第二垂直跑基线，**`pass_at_3` = 1.0**
（train 1.0、holdout 1.0），48 次运行全部 `attribution=none`。当时：

```
[3] 复制 attribution=knowledge_gap 且 split=train 的运行 → 复制 0 次
[4] C propose → proposal_state=no_change 提案 0 条
[6] C 回归门 → conclusion=no_change
```

按规格 E7：「无 skill 通过率是 0 或 1 → **不改提示词去凑数；写 blocker 检查 verifier**」。
按 E8：`knowledge_gap` 失败少于 1 条 → C 只交付夹具验收，**提案数保持 0**。

当时 `no_change` 是**正确产出**，不是失败：那条批次的卡没有可学的东西。

## 当时为什么「卡太容易」

抽查 `TP-S03-fact/1`（`live=true`）：

- `verdict.json`：`status=pass`、`overall_pass=true`、`attribution=none`、`live=true`。
- 断言只有两条：① 复述查询串；② 答案里出现来源站点域名 `govt.chinadaily.com.cn`。
- `artifacts/answer.json` 里模型**真的**复述了原问题、给了正确结论、并引用了
  `govt.chinadaily.com.cn`（Feb 25, 2025）。是**真通过**，不是误判。

那道题对 `ecnu-max` 是**一跳**：`lookup.json` 的检索产出已经把答案原文喂进上下文，
题面又明确要求「复述问题 + 引用来源」，模型照做即可。

## 为什么后来不再触发（口径没变，是样本变了）

日本线移除后，默认变体换成 trippal，A 线按 `--scenarios` 出的 16 张卡里
S01（签证合规）/ S03（移动支付）两张在真模型下**偶发**漏引来源站点 ——
48 次运行里出现 2 次 `knowledge_gap`。这不是改判据改出来的：verifier 一个字没动，
是检索产出在这些卡上不总是把来源喂全，模型于是偶尔「答对但没引来源」，
正好被 dev-005 的来源断言抓住，成了可学习的缺口。

## 结论

- **本 blocker 当前不成立**：基线 0.958、有 2 条 knowledge_gap、C 出 1 条提案、回归门 pass。
- 保留下文「若再触发 E7 该怎么处置」的清单，供将来基线又回到 1.0 时直接用。

## 若基线再次到 0/1（不要改提示词凑数）

1. **让检索产出不足以直接回答**：选用「页面只解决一半、需要跨来源合成」的场景
   （如 S01 签证合规：入境政策页 + 口岸实操页），而不是单页单跳。
2. **加可核对的第二断言**：断言答案里必须含页面的**生效日期**
   或某个只有细读正文才拿得到的具体字段，把「照抄结论句」与「真的读了来源」分开。
   注意：这条**不能**为了制造失败而加，必须来自夹具（dev-005 的口径：值来自
   `tool_data` 的 `source_url`/正文，不手写）。
3. **引入 `artifact_card`**：S03/S04/S12 这类「产出一份能照着做的清单」的场景
   本来就不该判 `fact_lookup`；它们需要 `contracts/v2`（见
   `b1-contracts-v2-trippal.md`）转正后才能正式建卡。
