# 足球比赛集锦 Skill

处理 XbotGo 或其他足球比赛 MP4：生成音频高光初剪，通过全时间轴复核整理射门集锦，保留原片时间和疑似攻门。

当前版本：**v1.1.0**。射门与空场尾段需要连续画面复核；不承诺全自动、无遗漏识别。

## 下载与安装

[下载 v1.1.0 skill 压缩包](releases/xbotgo-highlights-v1.1.0.zip)

解压后，把 `xbotgo-highlights` 目录放入 `~/.codex/skills/`，新开会话后说“用 xbotgo-highlights 处理这些足球录像”。

需要 Python 3.9+、ffmpeg/ffprobe；全时间轴抽样另需 Pillow。详细步骤见 [操作说明](xbotgo-highlights/references/workflow.md)。

## 使用示例

```bash
python3 xbotgo-highlights/scripts/highlight_tool.py auto /path/to/match.mp4 --out-dir /path/to/result --profile sevens
```

此命令是音频高光初剪入口。射门补漏、空场复核及加长剪辑请按 [SKILL.md](xbotgo-highlights/SKILL.md) 执行。

## 版本记录

见 [CHANGELOG.md](xbotgo-highlights/CHANGELOG.md)。

仓库仅分发 skill 与脚本，不含比赛录像、私钥或任务输出。
