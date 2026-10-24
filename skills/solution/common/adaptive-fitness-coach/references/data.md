# 数据与命令

Python 3.9+，仅标准库，不联网、不上传训练数据。由宿主 AI 将口语解析成 JSON 并通过 stdin 传入，不把用户原话插进 shell 命令。下文 SKILL_ROOT 代表本技能绝对目录，DATA 代表已确认的独立持久训练日志本地路径。调用后仍须通过宿主的持久文件能力写回；技能脚本不能自动保证跨设备同步。

## 命令形式（跨平台）

统一形式：`<python> SKILL_ROOT/scripts/coach.py OPERATION --data DATA`。

- `<python>` 按宿主环境选择：类 Unix 常为 `python3`，Windows 常为 `python` 或 `py`。
- **编码硬约定：stdin 与输出一律 UTF-8**（脚本内部强制，与系统 locale 无关）。宿主以 subprocess 传参时应将 bytes 以 UTF-8 编码（或显式指定 `encoding='utf-8'`），不要依赖 locale 默认编码。
- **首选宿主以 subprocess 等结构化方式传 stdin**（平台无关、无引号转义问题）。手写 shell 时再用下述示例。
- `inspect` / `review` 不读 stdin；其他命令读一个 JSON 对象。
- 成功打印 JSON 到 stdout；失败时 stderr 输出 JSON、退出码 2。**失败不得声称已记录。**
- 不要在公开仓库放实际日志。

bash（heredoc 定界符加单引号，防变量展开）：

```bash
python3 SKILL_ROOT/scripts/coach.py log --data DATA <<'EOF'
{"session_id":"...","set":{...}}
EOF
```

PowerShell（here-string；结束标记 `'@` 必须**顶格**，前面不能有空格；如遇编码问题可先执行 `$OutputEncoding = [System.Text.Encoding]::UTF8`）：

```powershell
@'
{"session_id":"...","set":{...}}
'@ | python SKILL_ROOT\scripts\coach.py log --data DATA
```

操作清单：`inspect` / `profile` / `plan` / `start` / `log` / `revise` / `amend` / `finish` / `review` / `purge`。日志 JSON 的字段结构见 [数据结构](schema.md)。

## 进步曲线分享卡片（card.py）

只读命令，不写日志、不联网。同一 `--data` 日志路径，另需 `--output`（SVG 绝对路径，建议放用户目录）：

```bash
python3 SKILL_ROOT/scripts/card.py --data DATA --output ~/progress-card.svg
python SKILL_ROOT\scripts\card.py --data DATA --output %USERPROFILE%\progress-card.svg   # Windows
```

- 可选 `--exercise <动作id>`（缺省自动选**有有效日志训练数最多**的正式动作，而非计划出现次数最多）、`--title <自定义标题>`。
- 输出自包含 SVG（依赖查看端系统字体渲染中文）；stdout 返回 JSON 摘要（卡片路径、动作、训练次数、RPE 记录比例）。日期无法解析的 session 会使摘要带 `warnings`（`unparseable_date: <date>`），卡片趋势文案同步降级为"部分日期无法解析"。
- 诚实性规则：只画已记录数据；RPE 全未知时如实展示“0/N”，不虚构平均 RPE；档位机器（无公斤负荷）不标注重量；同一动作不足 2 次完成训练时报错退出（码 2），不生成空卡片。次数递增但负荷非恒定时附注“负荷有变化，结合重量解读”。
- 趋势文案与 `review` 的 `long_gap` 语义一致：相邻记录间隔超过 21 天时标注“跨段记录，进步不可比”并附中断提示，不宣称逐次进步。卡片是记录呈现，不是进阶建议。

## 生命周期示例

`profile`：渐进合并档案。传值更新；显式传 `null` 表示删除该键。额外字段保留，不应主动收集无关敏感信息。
```json
{"goal":"hypertrophy","secondary_goals":["strength"],"experience":"intermediate","days_per_week":3,"minutes":60,"equipment":["gym"],"target_rpe":7,"limitations":"用户报告无当前疼痛","interaction":"on_demand"}
```
`plan`：宿主 AI 依据训练策略生成完整多日计划；例子仅含一个训练日。每次保存追加版本，额外字段如周期长度/原因/每周时间表允许保存。动作 id 需要稳定，同动作不同器械、幅度或计重方式用不同 id。条目可带 `planned_kind:"warmup"` 表示热身用途（如首个主项的逐渐加重热身组、独立热身动作），缺省视为正式条目；热身条目不计入完成率分母，也不进入 review 进阶评估，向其记录的组应标 `kind:"warmup"`。
```json
{"goal":"hypertrophy","reason":"首次建立计划","review_after":"三次可比训练后","days":[{"name":"背部","exercises":[{"id":"lat-pulldown-machine-a","name":"高位下拉","sets":3,"reps_min":8,"reps_max":12,"load_kg":null,"load_basis":"machine","rest_seconds":120,"target_rpe":7,"cues":["躯干稳定，避免借力后仰","下拉到可控范围"]}]}]}
```
`start`：使用用户当地日期（ISO 格式），session_id 唯一；只允许一个活动训练。日期须为扩展格式 `YYYY-MM-DD`（如 `2026-10-23`）；Python 3.11+ 会把基本格式如 `20261023` 接受并归一化存储，3.9/3.10 则直接拒绝。默认使用最新计划，可用 `plan_version` 指定历史版本，`day_index` 选择训练日（越界会被拒绝并提示合法范围）。
```json
{"session_id":"2026-10-23-back-1","date":"2026-10-23","day_index":0}
```
`log`：每组一个事件，允许实际重量/RPE未知；重复相同事件返回duplicate，数据不重复累计；同ID不同内容会报错。
```json
{"session_id":"2026-10-23-back-1","set":{"event_id":"2026-10-23-back-1-pull-1","exercise_id":"lat-pulldown-machine-a","kind":"work","load_kg":40,"load_basis":"machine","reps":10,"rpe":8,"quality":"unknown","safety":"none","feeling":"做了十个，最后两个费劲，前臂很酸"}}
```
quality 为 unknown/good/poor；good仅在用户明确报告动作稳定等证据时采用。safety 为 none/pain/urgent；先由 AI 分辨用户原话，不能把缺乏医疗危险检测的脚本当安全判断替代。允许 extra 字段保存 source、用户偏离建议的原因等。多组同一句反馈要分别记录，不把3组的总次数作为单组输入。

`revise`：更正已记录的组，训练结束后也可用（用户常在收尾后才发现记错）。输入 `{"session_id":"...","event_id":"待更正ID","set":{新内容}}`；新行须用新 event_id，原行标记 `superseded_by`，统计自动取链尾数据。已被更正的事件再次 revise 会被拒绝，需更正链尾。重复提交相同修订返回duplicate。
```json
{"session_id":"2026-10-23-back-1","event_id":"2026-10-23-back-1-pull-1","set":{"event_id":"2026-10-23-back-1-pull-1-rev1","exercise_id":"lat-pulldown-machine-a","kind":"work","load_kg":40,"load_basis":"machine","reps":9,"rpe":8,"quality":"unknown","safety":"none","feeling":"更正：实际9个"}}
```

`amend`：`{"session_id":"...","reason":"器械被占用","day":{...}}`；day结构同 plan.days 的单个元素，记录前一快照。必须保留已记录动作的条目和计重口径；剩余组数减少时 sets 表示当日调整后的总正式组目标，不是尚未完成组数，不能小于该动作已完成正式组数。替换剩余组时保留已做动作目标为已做数量、增加替代动作及其剩余目标。warmup记录也保留原动作条目。

`finish`：`{"session_id":"...","status":"completed","summary":"握力较疲劳，未额外加组"}`。提前结束用aborted。review仅使用completed训练，aborted仍保留供人工分析。缺少动作、日志或RPE的情况必须在结果中说明，不虚构。输出同时提供相对原定计划（`completion_ratio_original`）与相对调整后计划（`completion_ratio`）的完成率。

`review`：按动作id、计重口径、次数范围、目标RPE聚合，输出按日期排序的最近三次完成训练（`entries` 含 session_id/date/day/gap_days 及 work_sets、mean_reps、mean_rpe、rpe_known_ratio、load_kg 聚合摘要）、候选决策（`decision`，含 `reps_progressing_in_range`：次数区间内逐次进步）与未达进阶条件的原因清单（`blocked_by`，含 `long_gap`：相邻间隔超过阈值）；`same_day_sessions` 为 true 表示其中存在同日多训，不能当作独立恢复周期。间隔阈值可由 `profile` 的 `long_gap_days` 调整（1–365 整数，缺省 21），review 顶层回显实际生效值 `long_gap_days_used`。存在日期无法解析的 session（通常为手改遗留数据）时，顶层附 `warnings`（`unparseable_date: <session_id>`）——此时排序与间隔仅供参考，先修复数据。不直接改计划，宿主 AI 仍须审核训练完整性和情境。`inspect`输出完整数据库，供重建上下文。

`purge`：用户明确要求删除数据时使用，必须显式确认。`{"confirm":"PURGE"}` 清空全部；`{"scope":"sessions","session_ids":["..."],"confirm":"PURGE"}` 删除指定训练。成功后连同 `.bak` 备份一起清除；外部同步副本和导出件需提醒用户自行删除。

## 单位与器械口径

`load_kg` 统一记录公斤：磅制器械换算后记录（保留一位小数），原始读数与单位存 extra（如 `{"source_load":45,"source_unit":"lb"}`）。机器档位不填 load_kg（null），档位字符串存 extra，不参与容量计算。单手哑铃口径不默认乘2，自重和辅助动作只统计组次而不计算伪公斤容量，机器容量仅在同机内比较。

## 未知原计划的补记
使用 `profile` 的 `unplanned_records` 字段保存历史补记，不创建虚构计划。例如 `{"unplanned_records":[{"id":"import-1","date":null,"plan_unknown":true,"sets":[{"exercise":"下拉","load_kg":40,"load_basis":"machine","reps":12,"rpe":null,"feeling":"很费劲"}]}]}`。更新列表时先 inspect 并合并去重，不覆盖已有条目；该字段是档案中的原始记录，不由 review 自动计算进阶或完成率。

## 统计口径
正式组训练容量按动作和计重口径分别计算 Σ重量×次数。完成率=实际正式组数/目标正式组数（`completion_ratio` 相对调整后计划、`completion_ratio_original` 相对原定计划），可大于1，不截断。原始计划保存在original_plan，调整后在plan，不抹掉原目标。每组实际RPE长期保留；target_rpe是目标、mean_reported_rpe是已报告值平均，rpe_coverage表示已知比例，不将缺失填零。被更正（superseded_by）的旧组不计入统计。

**遗留数据迁移（planned_kind 引入前的旧日志）**：旧 session 中向热身动作记录的组，在 `planned_kind` 字段引入前缺省视为正式条目，会稀释历史 session 的完成率展示；session 已结束后无法 amend 纠正。如需修正历史展示，可在备份数据后手动为该日志 plan/条目补 `"planned_kind":"warmup"`（仅建议对明确记得是热身的条目操作）；review 进阶评估不受影响（按现行口径重新聚合）。

## 可靠性
写入使用临时文件、fsync、原子替换、单写者锁，保留上一版本.bak。0o600 权限保护仅类 Unix 生效，Windows 依赖用户目录本身的访问控制。进程崩溃可能留下.lock目录；先确认没有活跃写入且检查日志/备份再清理，不自动删除。JSON损坏或不支持版本时停止写入，不重置。路径应在用户可控且持久的位置；备份同样含敏感数据。用户要求删除日志时用 `purge`，并提醒处理外部同步版本。脚本没有后台服务、提醒、动作视觉分析或设备接口。
