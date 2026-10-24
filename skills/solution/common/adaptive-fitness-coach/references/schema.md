# 数据结构

训练日志（`--data` 指向的 JSON）的完整字段参考。配合 `inspect` 输出重建上下文使用。标注“开放”的字段可由宿主 AI 按需扩展，脚本原样保留；其余字段由脚本管理。

## 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| schema_version | int | 当前为 1；不匹配时脚本拒绝写入 |
| profile | object | 用户档案（见下） |
| plans | array | 计划版本历史，按保存顺序 |
| sessions | array | 训练记录，按创建顺序 |

## profile

开放结构。常见键：goal、secondary_goals、experience、days_per_week、minutes、equipment、target_rpe、limitations、interaction。脚本校验 target_rpe（1–10）与 long_gap_days（1–365 整数，相邻可比训练间隔阈值，缺省 21；传 null 删除恢复默认）。`unplanned_records` 为补记列表（见 data.md），每条含 id、date（可 null）、plan_unknown:true、sets、feeling。

## plans[i]

| 字段 | 类型 | 说明 |
|---|---|---|
| version | int | 序号，从 1 开始 |
| created_at | string | UTC ISO 时间 |
| plan | object | 原样保存的 plan 输入：goal、reason、review_after 等开放字段 + days |

plan.days[j]：name + exercises。每个动作：

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| id | string | 唯一 | 同动作不同器械/幅度/口径用不同 id |
| name | string | — | 展示名 |
| sets | int | 1–20 | 正式组目标 |
| reps_min / reps_max | number | 1–100 | 次数范围，min ≤ max |
| load_kg | number/null | 0–1000 | 未知为 null；磅制换算公斤，机器档位不填 |
| load_basis | string | total / per_hand / machine / bodyweight / assisted | 计重口径 |
| rest_seconds | number | 15–600 | 组间休息 |
| target_rpe | number | 1–10 | 目标强度 |
| planned_kind | string | work / warmup | 条目用途；缺省 work（兼容旧数据）。warmup 条目不计入完成率分母，也不进入 review 进阶评估 |
| cues | array | — | 技术提示（开放） |

## sessions[i]

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 即 session_id，全局唯一 |
| date | string | 用户当地日期（ISO），由 start 固定 |
| status | string | active / completed / aborted |
| plan_version | int | start 时实际使用的计划版本 |
| original_plan | object | 当日计划的原定快照（day 结构） |
| plan | object | 当前生效的当日计划；amend 后更新 |
| changes | array | 每次 amend 的 {reason, previous}；previous 为调整前快照 |
| logs | array | 逐组记录（见下） |
| summary | string | finish 时填写 |

## logs[k]（组记录）

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| event_id | string | 全 session 唯一 | 幂等键；修订行用新 id |
| exercise_id | string | — | 须存在于当日 plan.exercises |
| kind | string | work / warmup | 热身不计入完成率 |
| load_kg | number/null | 0–1000 | 实际负重 |
| load_basis | string | — | 须与计划一致 |
| reps | int | 0–1000 | 实际次数 |
| rpe | number/null | 1–10 | 用户报告值；未知为 null，不虚构 |
| quality | string | unknown / good / poor | good 仅在明确证据时采用 |
| safety | string | none / pain / urgent | 由 AI 先于脚本分类 |
| feeling | string | — | 用户原话 |
| revises | string | — | 仅 revise 命令写入：指向被更正的 event_id |
| superseded_by | string | — | 仅 revise 命令写入：指向替代行；带此标记的行不计入统计 |
| （其余） | — | — | 开放字段，如 extra、source、偏离原因 |

## review 输出（非持久数据）

每个 exercise_reviews 条目：exercise_id、load_basis、decision（insufficient_data / maintain_and_observe / reps_progressing_in_range / review_recovery_and_reduce / consider_smallest_load_increment）、entries（按日期排序的最近三次，每条含 session_id、date、day、gap_days（与前一条目的间隔天数，首条为 null）、work_sets、mean_reps、mean_rpe、rpe_known_ratio、load_kg）、blocked_by（未达进阶条件的原因清单，含 long_gap：相邻间隔超过 profile.long_gap_days）、load_not_tracked（不以公斤追踪负荷）、same_day_sessions（是否存在同日多训）。仅 planned_kind 为 work（或缺省）的条目参与聚合。顶层另有 long_gap_days_used（本次实际生效的间隔阈值）。reps_progressing_in_range 表示三次 mean_reps 在次数区间内严格递增：进步仍在进行，维持重量，预计触顶后可考虑最小进阶。

## 磁盘伴随文件

主日志旁可能存在：`.bak`（上一版本备份，purge 时一并删除）、`.lock`（写入锁目录，崩溃残留时先确认无活跃写入再清理）。两者同样含敏感数据。
