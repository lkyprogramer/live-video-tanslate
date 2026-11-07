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

```sh
uv sync --project engine --frozen
uv run --project engine --frozen pytest -q engine/tests
npm --prefix extension run typecheck
npm --prefix extension test
```

目前是试用原型。已在本地 HTML5 视频和 YouTube 验证字幕上屏与后端切换。17 段原速回放的 5 秒语义正确覆盖率为 54.5%，低于 90% 验收门槛。仓库保留验收摘要，原始素材、完整运行日志、模型和本机配置由各自环境生成。[验收与资源数据](docs/v1-acceptance.md) · [开发方案](docs/development-plan.md) · [v1 接口合同](docs/subtitle-backend-contract.md)

日语识别、日中翻译和实时链路的后续对照见[实验方案](docs/optimization-plan.md)与[本机实验结果](docs/optimization-results.md)。
