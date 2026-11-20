# 赛后使用 SoccerNet

按队筛选请用 [球队动作识别](teamspotting.md)；个人或门将任务使用 [球员跟踪](tracking.md)。

## 给本地录像打标签

从技能目录运行。一个命令处理一个原片；多段任务按拍摄顺序逐个执行，保留各自的源内时间。遇到同一原片和裁剪副本时按任务范围选其一，避免重复计数。

```bash
python3 scripts/calf_spot.py "/path/to/match.mp4"
```

默认在原片旁新建结果目录，写入看回放.html、候选.csv、预测.json。指定 --out-dir 可以集中存放结果。网页默认显示全部 Goal 提示，--shots 同时显示射正、射偏。每个按钮从事件前 10 秒播放原片。移动文件时保留页面和原片的相对位置。

CALF 按每秒两帧抽样并取画面中央，用比赛上下文预测时间。短片不足 120 秒时用末帧补齐模型输入，只保留实际时长内的事件。远端球门、遮挡、球出画和等待重开都会影响判断。筛选任务交付候选与回放页；需要成片时按既有判断规则登记已确认事件，再剪辑。零候选仅表示模型此次没有给出该标签。

## 本机环境

运行器使用 ~/.local/share/xbotgo-highlights/calf-v1 的权重和 Python 环境，Apple Silicon 上优先用 MPS 提取画面特征。日常处理在本机运行。新机器首次配置需要 uv、ffmpeg、联网下载模型和依赖，执行：

```bash
python3 scripts/calf_setup.py
```

模型来源与算法说明见 [SoccerNet CALF](https://github.com/SoccerNet/sn-spotting/tree/main/Benchmarks/CALF)。
