# IELTS 固定 MP3 试点资源

当前 `manifest.json` 的 `assets` 为空：**尚未生成或接入高质量 MP3，未进行真人音质试听**。这里没有占位 MP3；不要把旧 eSpeak 文件复制到这里后称为高质量试点录音。

五段原文和题目在 `learning/ielts_listening_pilot.json`。取得有使用权、经人工试听的五段真实 MP3 后，用 `tools/prepare_ielts_audio.py install` 验证并写入此目录，详见 `docs/ielts-mp3-pilot.md`。资源随代码构建，不在 Render 运行时生成或写盘。正式资源 URL 使用内容 SHA-256，替换音频必须产生新 URL，不得覆盖同名文件。
