# 按球队筛射门和进球

使用官方 sn-teamspotting 提供的 T-DEED 球队动作权重。模型同时估计 12 类动作和执行动作的队伍，包括传球、传中、封堵、射门和进球。每次运行处理一个本地视频。

## 首次准备

从技能目录运行：

```bash
python3 scripts/team_setup.py
```

需要 Git、uv、Python 3.11 和 ffmpeg。安装程序把固定版本的官方代码、独立 Python 环境和校验过的权重放入 `~/.local/share/xbotgo-highlights/team-v1`。首次准备需要联网，之后原片分析使用本地文件。已有官方权重时可传 `--weights "/path/to/checkpoint_best.pt"`。[官方代码和权重入口](https://github.com/SoccerNet/sn-teamspotting/tree/091fed2fc35c33f7489f3596958a2fe385e37d65) · [上游代码许可 GPL-3.0](https://github.com/SoccerNet/sn-teamspotting/blob/091fed2fc35c33f7489f3596958a2fe385e37d65/LICENSE)

## 使用

```bash
python3 scripts/team_spot.py "/path/to/match.mp4"
```

默认保留两队的候选。在原片旁生成 `TDEED-…/看回放.html`、`球队动作.csv` 和 `预测.json`。网页显示疑似射门和疑似进球，点时间从动作前 8 秒播放。表格保留高于设定阈值且去除相邻重复的各类事件、源内秒数、原片、模型球队和模型分数。填入 `--out-dir` 可以改变输出位置。默认使用 CPU。

官方的 left/right 表示完成动作的左队或右队；同一队员跑到画面另一侧仍属于同一队。上游 README 将两队称为 “left or right in the video”，未进一步指定通用的攻向或守门方向。用几个已辨认球衣、控球队伍的片段核对模型标签和我方的对应关系。未经核实时保留两队和未知身份。[官方定义](https://github.com/SoccerNet/sn-teamspotting/blob/091fed2fc35c33f7489f3596958a2fe385e37d65/README.md) · [上游标签编码](https://github.com/SoccerNet/sn-teamspotting/blob/091fed2fc35c33f7489f3596958a2fe385e37d65/util/dataset.py)

例如已确认前半段我方对应 left，原片第 1800 秒开始改为 right：

```bash
python3 scripts/team_spot.py "/path/to/match.mp4" --our-side left --switch-at 1800
```

此时网页只列映射到我方的射门和进球候选，CSV/JSON 仍保留双方，便于纠正球队判断。分文件录制时，球队与换边按各自原片时间重新对应。默认最低联合分数 0.10；可用 `--threshold 0.03` 扩大候选。模型时间轴为每秒 12.5 次采样，采用 100 帧滑窗和 75 帧重叠。输出为本地复核用途，使用逐类峰值去重；进球结果与球员身份由连续画面复核后登记。
