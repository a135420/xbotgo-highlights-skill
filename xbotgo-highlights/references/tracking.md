# 指定球员和门将的轨迹

使用 SoccerNet Tracking 2022 无标注框基准所指定的 YOLOX-X MOT20 人员检测权重和 ByteTrack。输入本地视频，输出临时轨迹编号、跟踪框及源内时间。模型会检出画面里可见的人，可能包括裁判、场边替补和背景人物。

## 首次准备

在 skill 根目录运行。需要 uv、ffmpeg、ffprobe 和 Python 3.11；uv 可准备 Python 环境。默认将官方权重、固定提交的源码和独立环境保存在 `~/.local/share/xbotgo-highlights/tracking-v1`，首次下载权重约 793 MB。安装后分析可以离线运行。

```bash
python3 scripts/tracking_setup.py
```

已有官方权重时可传 `--weights "/path/to/bytetrack_x_mot20.tar"`。脚本校验完整 SHA-256。源码来自 ByteTrack 的 MIT 授权版本，原始许可文件保留在安装目录。仓库发布适配脚本，权重从作者公开地址单独下载。[SoccerNet 参考流程](https://github.com/SoccerNet/sn-tracking/tree/main/Benchmarks/ByteTrack) · [ByteTrack 代码和权重](https://github.com/ifzhang/ByteTrack/tree/d1bf0191adff59bc8fcfeaa0b33d3d1642552a99)

## 跟踪并复核身份

```bash
python3 scripts/tracking_run.py "/path/to/match.mp4" --start 180 --duration 30
```

初次只检查包含已知球员的 30 秒，可用 `--start` 和 `--duration` 指定原片区间。省略二者可跑整段。默认每秒分析 8 帧，可用 `--fps` 调整跟踪密度；快速移动或遮挡较多时，提高采样率往往更有利于连续关联，耗时也会增加。默认写入原片旁独立目录，`--out-dir` 可设置结果父目录。

打开“看轨迹.html”播放原片，结合用户确认的参考画面认准目标，选择画面中的编号即可单独显示该轨迹。可跳转到所选编号首次出现的时间。“tracks.csv”按采样帧列出源内秒数、ID、原画面像素框和分数。“跟踪摘要.json”列出各编号首末出现时间、实际可见帧数以及本次采样配置。页面按需读取同目录 boxes-*.js；移动时保留结果目录和原片之间的相对位置。

一个编号仅代表本次调用中的轨迹。出画、遮挡、换镜头、跨文件或分段重新运行都可能产生新编号或换人。做个人集锦时，结合用户认可的身份锚点、球衣细节与连续动作复核；分段记录确认过的编号和源内时段，再用现有事件工作台导出动作。轨迹本身没有“触球”“扑救”或“进球”语义。

Apple Silicon 通过 MPS 运行检测器，CPU 完成关联与 NMS。基准使用逐帧 CUDA 推理；本入口保留同一检测权重和跟踪算法，采用可调采样率、本机解码以及网页叠框，正式挑战集成绩不适用于此运行方式。
