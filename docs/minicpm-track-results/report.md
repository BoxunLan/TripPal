# localskill 汇总

运行总数（含未计入分母的）：332

| 指标 | C-seed-skill |
|---|---|
| task_count | 332 |
| pass_at_3 | 0.768072 |
| mean_assertion_pass（连续指标，比 pass@3 稳） | 0.911828 |
| assertions_passed/总断言数 | 1096/1213 |
| train_pass_at_3 | 0.79918 |
| holdout_pass_at_3 | 0.681818 |
| avg_tokens（平均 input_tokens+output_tokens） | 10220.428 |
| avg_input_tokens | 8596.048 |
| avg_output_tokens | 1624.38 |
| attribution_counts（仅计入分母的运行） | {"knowledge_gap": 55, "none": 255, "execution_defect": 22} |
| failure_kind_counts（attribution 的细分：http_402 / budget_exhausted / format_contract …） | {"knowledge_gap": 55, "none": 255, "budget_exhausted": 22} |

全部运行的归因分布（含 live=false / contaminated）：{"knowledge_gap": 55, "none": 255, "execution_defect": 22}
全部运行的 failure_kind 分布（attribution 的细分，旧产物回退到 attribution）：{"knowledge_gap": 55, "none": 255, "budget_exhausted": 22}

## examples

### C-seed-skill

- `V2A-faq-01` run 1 · status=fail · attribution=knowledge_gap · `C:/Users/17641/AppData/Local/Temp/v2-local-skill/runs/localskill/C-seed-skill/V2A-faq-01/1/verdict.json`
- `V2A-faq-02` run 1 · status=fail · attribution=knowledge_gap · `C:/Users/17641/AppData/Local/Temp/v2-local-skill/runs/localskill/C-seed-skill/V2A-faq-02/1/verdict.json`
- `V2A-faq-03` run 1 · status=fail · attribution=knowledge_gap · `C:/Users/17641/AppData/Local/Temp/v2-local-skill/runs/localskill/C-seed-skill/V2A-faq-03/1/verdict.json`
