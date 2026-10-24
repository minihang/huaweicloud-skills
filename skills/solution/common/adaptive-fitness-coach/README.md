# adaptive-fitness-coach

中文自适应健身教练 Skill。为 AI 宿主（Codex、ChatGPT、API 及任何支持 Skill 的环境）提供力量训练的长期计划、逐组陪练与记录能力：理解“60做了8个，最后两个费劲”这类口语反馈，保存 JSON 训练日志，依据多次可比训练给出保守的进阶候选建议，并生成进步曲线分享卡片（SVG，可晒图）。

**版本**：0.5.0（Round 3 评审修复：rest 建议上限 300→600 与 schema 对齐（P1）；review/card 对不可解析日期输出 warnings 不再静默（P2）；测试加 Python 3.11 版本守护；热身低次数爬坡不再误触发降载建议；卡片自动选择按有效日志数排序；负荷非恒定时趋势附注；遗留热身条目迁移指引；清理宿主运行时残留目录。0.4.0：进步曲线分享卡片 `card.py`、测试套件 subprocess 超时保护、冷启动"草案先行"与 `training.md` 按需加载、宿主适配声明。0.3.0：`long_gap` 进阶阻断与可调阈值、review 聚合摘要、`reps_progressing_in_range`、热身条目隔离 `planned_kind`）

## 功能

- 不同经验水平的增肌 / 最大力量 / 减脂保肌 / 运动表现 / 综合体能长期计划
- 冷启动草案先行：新用户首轮即得保守可执行的当日草案，不必先答三轮问题
- 逐组陪练：口语反馈解析、即时调整候选（降载、休息延长、动作替代）
- 器械占用、时间不足等现实约束的当日计划调整（amend）
- 逐组 JSON 记录与修订链（revise，不改写历史）、双口径完成率统计
- 三次可比训练聚合评估，输出候选决策与未达条件原因（blocked_by）
- **进步曲线分享卡片**：`card.py` 从真实日志生成 SVG 曲线卡，数据可晒、来源可查
- 数据删除（purge）与备份联动清理
- 仅力量训练；疼痛/危险症状优先停训提示；不提供医疗诊断

## 快速开始

1. 将本目录放入宿主的 skills 目录（或按宿主说明安装）。
2. 对宿主说“今天练背”或调用 `$adaptive-fitness-coach`。
3. 训练日志默认保存在用户数据目录（类 Unix `~/.local/share/adaptive-fitness-coach/journal.json`，Windows 用户目录下 `.adaptive-fitness-coach/journal.json`），也可指定路径。

## 命令行（供高级用户/调试）

```bash
python3 scripts/coach.py inspect --data <日志路径>            # 类 Unix
python scripts\coach.py inspect --data <日志路径>             # Windows
python3 scripts/card.py --data <日志路径> --output <卡片.svg>  # 进步曲线分享卡片
python3 scripts/test_coach.py                                  # 运行测试（临时数据，不碰真实日志）
python3 scripts/test_card.py                                   # 卡片测试
```

测试子进程带超时保护（默认 60 秒/次，`AFC_TEST_TIMEOUT` 可调），挂起环境快速失败而非无限阻塞。

其余操作（profile / plan / start / log / revise / amend / finish / review / purge）通过 stdin 传 JSON（编码一律 UTF-8），见 `references/data.md`。

## 宿主适配

- 主清单 `SKILL.md` 与脚本（Python 3.9+ 标准库）宿主无关，可在任何支持本地文件与命令执行的 Skill 环境运行（Codex、ChatGPT、API，以及华为云等平台侧支持 Skill 的宿主）。
- `agents/openai.yaml` 仅为 OpenAI 系宿主的界面元数据，`allow_implicit_invocation: false` 表示该宿主下由用户显式调用；不影响其他宿主加载本技能。
- 欢迎为其他宿主补充各自的元数据文件；训练日志路径与解释器按 SKILL.md 的“术语与宿主约定”适配。

## 隐私

训练日志仅存本地 JSON，不联网、不上传。日志含原话等敏感内容，请勿提交公开仓库；删除用 purge 并自行处理同步副本。备份 `.bak` 同样含敏感数据。

## 测试

```bash
python3 scripts/test_coach.py
python3 scripts/test_card.py
```

共 54 个用例：39 项核心（幂等记账、修订链、安全信号、双口径统计、review 排序与 blocked_by、long_gap 阻断与可调阈值、进步决策、purge 确认与备份清理、原子写入、文件损坏保护、UTF-8 编码回归、热身条目隔离、rest 上限 600、垃圾日期 warnings、版本守护）＋ 15 项卡片（数据聚合、热身/被更正行排除、XML 转义、long_gap 与垃圾日期文案、不虚构 RPE、auto-select 按有效日志、CLI 契约）。全部子进程带超时保护。

## 免责声明

本技能提供训练组织与记录辅助，不是临床或训练效果验证过的专业处方系统；具体训练范围受 ACSM 抗阻训练立场声明原则启发，但脚本内的降载/进阶参数是工程启发式。出现疼痛、胸闷、晕厥等症状请停止训练并寻求专业帮助。

## 许可证

[MIT](LICENSE) © 2026
