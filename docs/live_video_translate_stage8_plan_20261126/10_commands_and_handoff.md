# 10 · 命令、实际执行边界与交接

**不要整页复制执行。**“已有”不代表适合立即在生产目录跑；“Stage8新增”必须先依T0–T7实现。以下是实现者的操作合同，本轮并未执行。

## 1. 只读预检（已有git及文件）

在用户明确指定的本地仓库目录执行：

```bash
git status --short
git rev-parse HEAD
git log -8 --date=iso-strict --format='%h %cd %s'
git show --stat 7ea13865c8c3673a1463495058a200723b153111
git diff --stat cdbf0a732cf1dead66df98d1696dfeb3390f9391 7ea13865c8c3673a1463495058a200723b153111
```

若SHA不可达，在已有权限内获取所需提交；不重置本地HEAD、不覆盖profile、不对用户未提交修改checkout。当前实际HEAD超出本文时先记录新diff，并检查是否已经解决本文疑点，不能直接套旧补丁。

本机已有模型/编译资产需要找到真实路径后`shasum -a 256 <已验证文件路径>`，不要凭profile名猜模型。磁盘/内存只读：`df -h .`、`sysctl hw.memsize`、`sysctl vm.swapusage`；缺命令或权限写UNKNOWN。

## 2. 仓库已有测试

现README提供：

```bash
PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests p0/p2/tests p0/local_streaming
```

前提：本地venv已经存在且版本匹配；否则先申请/安排隔离依赖安装，不用此行暗中安装包。模型/硬件条件未满足的skip要汇总，不能声称全部真机路径通过。

扩展使用`extension/package.json`里当次锁定的test/build scripts。实施时先读文件确认命令，不根据旧记忆自行npm install或升级依赖。

已有native构建：`scripts/setup_nemo_ane.sh`。**它可能编译或准备外部资产，不属于只读预检**，本轮未运行。实际执行先审脚本、授权下载/构建、记录输出hash；不要以git pull成功代替native版本确认。

## 3. 已有Chrome harness的限制

`p0/perf_probe/verify_stage6_ep01_chrome.mjs`识别`MP4_PATH`、`OUT_DIR`、`EXT_DIAG_DIR`、`TEST_PROFILE`、`MAX_RUN_A_S`、`SKIP_RUN_B`；`MAX_RUN_A_S=0`为整集。

但原脚本的归档目录和部分临时路径固定。**不能直接在当前主工作区重跑覆盖旧Stage6归档。**T1创建Stage8 wrapper，把所有输出/归档/临时profile参数化，输出必须在唯一run目录，预先存在的结果目录默认拒绝覆盖。

音频/扩展诊断构建未准备时，runner明确BLOCKED；不可用伪造DOM事件替代浏览器。

## 4. Stage8新增CLI合同（当前不存在）

下面命令在T0–T7实现并测试后才可运行。`/ABS/...`是必须替换为本机已验证路径的占位，不是合法默认值；runner必须拒绝未替换值。

每个run各有一个锁定manifest；以下各命令是独立用法示意，不是共用同一个manifest连续覆盖。`--arm/--out/--duration-s`只能与manifest相同，不能临时改参数；冲突退出2。

```bash
PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.preflight \
  --manifest /ABS/stage8/run_manifest.json

PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.check_mt_cache \
  --manifest /ABS/stage8/run_manifest.json \
  --out /ABS/stage8/runs/cache-check-001

PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.bench_mt \
  --manifest /ABS/stage8/run_manifest.json --arm mt-b0 \
  --out /ABS/stage8/runs/mt-b0-001

PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.bench_asr \
  --manifest /ABS/stage8/run_manifest.json --arm asr-same-span \
  --out /ABS/stage8/runs/asr-same-span-001

node p0/stage8_eval/run_chrome.mjs \
  --manifest /ABS/stage8/run_manifest.json --duration-s 300 \
  --out /ABS/stage8/runs/chrome-b0-001

PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.aggregate \
  --runs /ABS/stage8/run_dirs.json \
  --units /ABS/stage8/semantic_units.jsonl \
  --judgments /ABS/stage8/adjudicated_judgments.jsonl \
  --thresholds /ABS/stage8/thresholds.locked.json \
  --out /ABS/stage8/aggregate_report.json

PYTHONPATH=.:engine/src engine/.venv/bin/python -m p0.stage8_eval.acceptance \
  --report /ABS/stage8/aggregate_report.json \
  --thresholds /ABS/stage8/thresholds.locked.json \
  --out /ABS/stage8/gate_decision.json
```

exit合同：0=该操作通过；2=输入/许可/资产/协议阻塞；3=运行完成但门禁FAIL；4=证据不足INCONCLUSIVE。性能runner产物仍须交给acceptance判断，不能仅因进程0退出就宣布默认档通过。runtime错误同时写error事件，禁止吞错改为缺样本。

`run_dirs.json`是绝对run目录字符串数组。aggregate验证三基线身份、相同observer/scorer与数据、人工标注/原始日志hash后输出机器报告；其数据有效状态不等于发布状态。只有acceptance的`gate_scope=G5`且指定profile的决定满足全部硬门禁，才可申请发布。

## 5. 输出目录合同

每run至少含 `manifest.json`、`environment.json`、`events.jsonl`、`summary.json`、`failures.jsonl`、`checksums.json`；有真机浏览器时另含`dom_events.jsonl`、`clock_anchors.jsonl`、`resource_samples.jsonl`。

质量评测另有`semantic_units.jsonl`、`blinded_judgments.jsonl`、`adjudication.jsonl`，记录reviewer类别与human_verified。公开档只保留hash、汇总、脱敏最小例子；完整影视文本和音频留在授权私有证据目录。

重新计算全部结果应能只凭锁定manifest与原始事件运行。缓存key包含规范化代码版本；任何临时手改计分也要更新key。

## 6. 给下一位AI的交接清单

- [ ] 指出当前repo SHA是否与本文一致，是否有新的本地提交/未提交改动。
- [ ] 列已存在资产与缺失资产，不执行下载。
- [ ] 列每个T任务的状态和真实测试命令/退出码；SKIP与PASS分开。
- [ ] 报告已证实的问题、未复现疑点、与理论/社区线索分开。
- [ ] 报告当前推荐档、代价、为什么不选其余候选。
- [ ] 有人类质量验证和真实DOM长跑才申请发布；没有就写证据还差什么。
- [ ] 若没有授权实施，交付计划到此为止，不把文档交付视为后续自动执行许可。
