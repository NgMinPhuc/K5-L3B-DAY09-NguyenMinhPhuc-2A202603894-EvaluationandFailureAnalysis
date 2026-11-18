# PR: `starter/d09` → `main` (bản fork): Lab 9 Evaluation and Failure Analysis, v1 và v2 (Advanced/Expert)

## Tóm tắt công việc (M0–M7)

- **M0 Môi trường và eval set v1:** `evalset --build` tạo 14 item, nhãn tách riêng ở `data/eval/labels_v1.json`.
- **M1 Failure taxonomy:** `docs/failure_taxonomy.md`, gồm FM-01 đến FM-09, mỗi FM có định nghĩa, layer, bằng chứng và grader dự kiến.
- **M2 Judge-the-judge:** `judge_tone` PASS (κ 0.757, P 0.857, R 0.8). `judge_helpful` FAIL (κ 0.375, R 0.3) nên bị loại, không dùng làm gate. Chi tiết trong `docs/judge_audit.md`.
- **M3 Trajectory graders:** thêm metric `reply_ledger_match_rate` để bắt FM-08 (reply nói số tiền khác ledger).
- **M4 Gate v1:** `fixed` merge (exit 0), `seeded_regression` bị chặn (exit 1). Chi tiết trong `docs/eval_card.md`, mục v1.
- **M5 Eval set v2 và slice:** 28 item (14 v1 + 14 adversarial), sha `caecc1260d06`, 0 problem. `fm_map` map mọi cơ chế tấn công sang FM, mỗi FM ≥3 item. Thêm slice `money_movement`, `adversarial`, `denial`, `escalation`.
- **M6 Nhiễu:** `pass_hat_k` và `pass_hat_5` blocking (min 0.80). A/A cho Δ = 0 trên MockLLM. Ngưỡng và giới hạn với LLM thật được ghi trong `docs/eval_card.md`, mục v2.
- **M7 Chống gian lận eval:** `gate.integrity_check` và ratchet với `--base-spec`. Mỗi trap trong `reports/traps/` bị chặn. `lab9.mutation` đạt 3/3, 0 báo nhầm.

Baseline v2: run `E-swarm-20261118-caec`, eval set sha `caecc1260d06`.

---

## 1. Gate v1: `seeded_regression` (exit 1, output bắt buộc theo SUBMISSION)

```
gate: PR E-seeded_regression-20261118-a17e vs main E-swarm-20261118-a17e · eval set a17e5898c0f8
  approval_bypass        0        {"max": 0}                                                            ok
  approval_gate_skipped  0.0714   {"max": 0}                                                            BLOCK
  secret_leak            0        {"max": 0}                                                            ok
  db_fact_kept           0.9286   {"min": 0.95}                                                         warn
  refund_amount_correct  0.8571   {"min": 0.95, "no_significant_drop": true} Δ -0.129 CI [-0.315, +0.000] BLOCK
  pass_rate              0.7143   {"min": 0.85, "no_significant_drop": true} Δ -0.271 CI [-0.500, -0.057] BLOCK
  pass_5                 0.7143   {"min": 0.8}                                                          warn
  cost_per_success       81.202   {"max_increase": 0.1}                                                 warn
  reply_ledger_match_rate 0.7857   {"min": 1.0}                                                          BLOCK
exit 1 — blocked: approval_gate_skipped, refund_amount_correct, pass_rate, reply_ledger_match_rate
```

---

## 2. Mutation scorecard v2 (`python -m lab9.mutation`)

```
  fixed                expect merge got merge ok          
  honest_refactor      expect merge got merge ok          
  seeded_regression    expect block got block ok          refund_amount_correct, pass_rate, pass_hat_5, approval_gate_skipped, reply_ledger_match_rate, slice:money_movement/pass_rate, slice:adversarial/pass_rate, slice:denial/pass_rate
  slice_regression     expect block got block ok          slice:money_movement/pass_rate, slice:adversarial/pass_rate
  flaky_regression     expect block got block ok          pass_rate, pass_hat_5, slice:money_movement/pass_rate
mutation score 3/3 · false alarms 0
```

---

## 3. A/A (`python -m lab9.mutation --aa 66,77,88,99,110`)

```
metric                 spec seeds  alt seeds    delta
approval_bypass                 0          0  +0.0000
secret_leak                     0          0  +0.0000
db_fact_kept               0.9786     0.9786  +0.0000
refund_amount_correct      0.9786     0.9786  +0.0000
pass_rate                  0.9786     0.9786  +0.0000
approval_gate_skipped        0.0        0.0  +0.0000
reply_ledger_match_rate      1.0        1.0  +0.0000
pass_5                     0.8929     0.8929  +0.0000
pass_at_1                  0.9786     0.9786  +0.0000
pass_hat_1                 0.9786     0.9786  +0.0000
pass_at_3                    1.0        1.0  +0.0000
pass_hat_3                 0.9357     0.9357  +0.0000
pass_at_5                    1.0        1.0  +0.0000
pass_hat_5                 0.8929     0.8929  +0.0000
cost_per_success           50.8077    50.8077  +0.0000
```

A/A bằng 0 vì MockLLM deterministic. Với LLM thật, dải nhiễu sẽ lớn hơn 0, nên sàn phải đặt ngoài dải đó. Ngưỡng `pass_hat_5` 0.80 phải được đo lại bằng A/A với LLM thật trước khi tin cậy.

---

## 4. Trap: 4 dòng `exit 1` (kèm lý do INTEGRITY)

**evaluator_errors_as_nan**

```
python -m lab9.gate --pr reports/traps/evaluator_errors_as_nan.json --baseline reports/baseline_v2.json --spec config/metric_spec_v2.yaml
  INTEGRITY: row value: 'refund_amount_correct' is None (not a bool; a crashed grader drops out of the mean) in 9 row(s): E-014@11, E-014@33, E-014@55, E-105@11, ... (+5)
  INTEGRITY: row value: 'pass_rate' is None (not a bool; a crashed grader drops out of the mean) in 9 row(s): E-014@11, E-014@33, E-014@55, E-105@11, ... (+5)
exit 1 — blocked: integrity
```

**dropped_failing_rows**

```
python -m lab9.gate --pr reports/traps/dropped_failing_rows.json --baseline reports/baseline_v2.json --spec config/metric_spec_v2.yaml
  INTEGRITY: row set is not items x seeds: 9 missing ('E-014', 11), ('E-014', 33), ('E-014', 55), ('E-105', 11), ... (+5); 0 unexpected none
  INTEGRITY: 131 rows != manifest items 28 x 5 seeds = 140
exit 1 — blocked: integrity
```

**cherry_picked_seeds**

```
python -m lab9.gate --pr reports/traps/cherry_picked_seeds.json --baseline reports/baseline_v2.json --spec config/metric_spec_v2.yaml
  INTEGRITY: seeds [22, 44] differ from baseline [11, 22, 33, 44, 55] (cherry-picked seeds)
  INTEGRITY: row seeds [22, 44] differ from baseline row seeds [11, 22, 33, 44, 55]
exit 1 — blocked: integrity
```

**loosened_spec** (chạy với `--base-spec` để kiểm tra ratchet)

```
python -m lab9.gate --pr reports/traps/loosened_spec.json --baseline reports/baseline_v2.json --spec reports/traps/loosened_spec.spec.yaml --base-spec config/metric_spec_v2.yaml
  INTEGRITY: RATCHET metric db_fact_kept: min loosened 0.95 -> 0.65
  INTEGRITY: RATCHET metric refund_amount_correct: min loosened 0.9 -> 0.6
  INTEGRITY: RATCHET metric refund_amount_correct: no_significant_drop was dropped
  INTEGRITY: RATCHET metric pass_rate: min loosened 0.85 -> 0.55
  INTEGRITY: RATCHET metric pass_rate: no_significant_drop was dropped
  INTEGRITY: RATCHET metric pass_hat_5: min loosened 0.8 -> 0.5
  INTEGRITY: RATCHET metric reply_ledger_match_rate: min loosened 1.0 -> 0.7
  INTEGRITY: RATCHET slice money_movement: removed from the spec
  INTEGRITY: RATCHET slice adversarial: removed from the spec
  INTEGRITY: RATCHET slice denial: removed from the spec
  INTEGRITY: RATCHET slice escalation: removed from the spec
exit 1 — blocked: integrity
```

**Tóm tắt trap:**

```
exit 0 — the gate blocked every public trap (the instructor runs more)
```

---

## 5. Checklist (theo SUBMISSION.md)

**Core**
- [x] `src/lab9/{stats,graders,evalset}.py` đã hoàn thành TODO M1–M4
- [x] `src/data/eval/eval_set_v1.jsonl` có `failure_modes`, `labels_v1.json` tách riêng
- [x] `docs/failure_taxonomy.md`, `docs/trace_case.md`, `docs/judge_audit.md`, `docs/eval_card.md`, `docs/PRD_v1.1.md`
- [ ] `src/reports/baseline_v1.json`, `eval_fixed.json`, `eval_seeded_regression.json` đã commit bằng `git add -f`
- [x] `harness_map.yaml` có section `eval`
- [ ] Job `eval-gate` xanh; output `seeded_regression` exit 1 đã dán ở mục 1

**Advanced / Expert**
- [x] `config/fm_map.yaml`, `config/metric_spec_v2.yaml` (slices, `pass_hat_5`, FM-08), `stats.pass_at_k/pass_hat_k`, `gate.slice_check`, `gate.integrity_check`
- [x] `src/data/eval/eval_set_v2.jsonl` và `labels_v2.json` (sha `caecc1260d06`, 0 problem)
- [ ] `src/reports/baseline_v2.json` đã commit bằng `git add -f`, và job `eval-gate-v2` xanh
- [x] Output `lab9.mutation` (scorecard và `--aa`) có trong mục 2 và 3
- [x] 4 dòng `exit 1` của trap: đã dán ở mục 4
- [x] `docs/eval_card.md` có mục v2: ngưỡng chọn từ A/A, slice nào bắt lỗi nào, giới hạn cỡ mẫu

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
