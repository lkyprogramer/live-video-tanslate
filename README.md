# live-video-tanslate

在 Apple Silicon Mac 上运行的 Chrome 扩展，把当前标签页的日语音频实时翻译成简体中文字幕，并保留原声。扩展通过 WebSocket 将音频送到 `127.0.0.1:18765`。服务默认使用 ReazonSpeech 和 Hy-MT2，扩展设置中可以切换已启用的后端配置。

## 安装

需要 Apple Silicon Mac、Chrome 116 或更新版本、Python 3.12 和 `uv`。从源码构建扩展还需要 Node.js 22 和 `zip`。

1. 解压 `live-video-tanslate-0.1.0-chrome.zip`，在 Chrome 的 `chrome://extensions` 开启开发者模式，点击「加载已解压的扩展程序」，选择包含 `manifest.json` 的目录。也可以从源码构建并加载 `extension/dist`。
2. 准备本地服务依赖和模型，复制 `engine/config/profiles.example.toml` 为 `engine/config/profiles.toml`，把 `allowed_origin` 里的占位 ID 改为 Chrome 显示的扩展 ID。[本机配置与模型下载](docs/local-setup.md)
3. 在项目根目录启动服务，将服务打印的令牌文件中的内容填入扩展设置，刷新配置并保存。

   ```sh
   uv run --project engine --frozen subtitle-server --config engine/config/profiles.toml
   ```

4. 播放日语视频，点击扩展图标开始显示字幕。再次点击停止。启用第二种后端后，可以在设置中选择配置，下一次启动字幕时生效。

## 从源码打包

```sh
npm --prefix extension ci
npm --prefix extension run package
```

命令构建 `extension/dist`，并在 `artifacts/` 生成扩展 ZIP。ZIP 包含浏览器端文件，本地服务和模型按安装步骤准备。

## 验证与现状

下一轮工作以[本地滚动识别与字幕修订方案](docs/local-streaming-architecture-plan-2026-11-12.md)为入口：固定 ReazonSpeech + Hy-MT2 验证滚动调度，分别评估句级提交与草稿字幕。方案尚未实施；视频和参考字幕由用户提供，运行时不读取字幕轨、不使用云端。

```sh
uv sync --project engine --frozen
uv run --project engine --frozen pytest -q engine/tests
npm --prefix extension run typecheck
npm --prefix extension test
```

目前是试用原型。已在本地 HTML5 视频和 YouTube 验证字幕上屏与后端切换。17 段原速回放的 5 秒语义正确覆盖率为 54.5%，低于 90% 验收门槛。仓库保留验收摘要，原始素材、完整运行日志、模型和本机配置由各自环境生成。[验收与资源数据](docs/v1-acceptance.md) · [开发方案](docs/development-plan.md) · [v1 接口合同](docs/subtitle-backend-contract.md)

日语识别、日中翻译和实时链路的后续分阶段探索见[执行方案](docs/exploration-plan.md)，最新验收和资源对照见[E0–E5 探索结果](docs/exploration-results-2026-11-07.md)，既有对照见[本机实验结果](docs/optimization-results.md)。

P2后续测试以[修订执行包](docs/live-video-translate-p2-2026-11-07/README.md)为入口。[B0工程基线报告](docs/p2-b0-results-2026-11-08.md)记录当前模型复现、两轮原速回放、Chrome可读中文和资源对照；质量与人工参考的剩余缺口单列。

[B1首筛报告](docs/p2-b1-results-2026-11-08.md)记录入口补零、日文标点和PLaMo两尺寸的实际评分与CPU/GPU/共享内存；四组均未晋级，当前默认后端保留。

[B2首筛报告](docs/p2-b2-results-2026-11-09.md)记录SenseVoice INT8与whisper-ja760的同轮对照、评分和资源；两组仍未晋级。[B3首筛报告](docs/p2-b3-results-2026-11-09.md)补齐Voxtral连续音频流与Gemma4音频直译；八个核心配置均未通过预登记质量门槛，默认继续使用reazon-hy。[B4只输出中文对照](docs/p2-b4-gemma-zh-results-2026-11-09.md)在G0因中日混合/日文输出退出，正式176次与质量评分未启动。

本轮探索已收尾，未采用的模型和独立运行环境已清理；保留当前reazon-hy、实验源码和原始证据。[清理范围、离线烟测及复现说明](docs/local-runtime-cleanup-2026-11-09.md)。
