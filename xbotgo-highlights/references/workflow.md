# 操作说明

## 安装与移交

把压缩包中的 `xbotgo-highlights` 整个目录放进同事的 `~/.codex/skills/`，或支持本地 skills 的工具对应目录。已有同名目录时先备份后替换。重新开启会话后可说“用 xbotgo-highlights 处理这些足球录像”。其他 agent 产品是否自动发现该目录取决于其配置，也可以直接读取 SKILL.md。

基础剪辑只依赖 Python、ffmpeg/ffprobe，包含的脚本无需登录、Token 或外部 API。全时间轴图需要 Pillow：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install Pillow
```

下列命令以 skill 根目录为当前目录；Python 可替换为上述环境中的解释器。素材不随包提供。

## 本次配置与通用逻辑

每次任务另建 `session.json`，示意结构如下；时间为源文件内秒数。它是复核记录约定，不是 auto 命令直接读取的配置。

```json
{
  "sources": [
    {"id":"part1","file":"/path/to/match.mp4","duration":1800}
  ],
  "excluded_ranges": [],
  "goal_references": [],
  "reviewed_ranges": [],
  "events": []
}
```

排除区间应增加 `source/start/end/reason/status/evidence`。`status` 采用 `confirmed` 或 `uncertain`；只有 confirmed 可用于跳过。用户明确指定范围时记录来源为用户指示。前7分钟、最后一个文件为空场、七人场尺寸、某场门框像素位置均不作为通用默认值。

## 排序和全时间轴检查

```bash
.venv/bin/python scripts/timeline_review.py "/path/to/part-b.mp4" "/path/to/part-a.mp4" \
  --out-dir "/path/to/timeline" --interval 2
```

输出 manifest.json 与分页图。时间标签是原片偏移；页内每一帧有索引。默认覆盖每个输入的全片，尾部不足一个抽样间隔时补最后一帧。无拍摄时间的文件按文件名排序，并在 manifest 中提示需核对；不要将该回退顺序说成已确认拍摄顺序。读取 JPG 后登记已看页数；如动作位于两个采样点之间，应补密集连续图或短片，不能据此认定无动作。

工具不自动判空场，也不会删除素材。先从时间轴发现疑似尾段，再放大检查“有人比赛 → 人员离场 → 持续空场”前后过程。确认后在 session.json 保存证据和精确区间。对单文件音频扫描，可用 `--ignore-end` 指定**从文件末尾往前排除多少秒**，不是排除区间的开始秒数：

```bash
python3 scripts/highlight_tool.py scan -i "/path/to/match.mp4" \
  --out-dir "/path/to/candidates" --profile sevens \
  --limit 0 --suppress-gap 0 --ignore-end 45
```

这里45秒仅为命令示例，须替换为本次已确认长度。整段空场文件只从本次待剪列表中移出；保留原片和排除记录。中间休息应分成比赛区间处理并保留原片偏移，不能简单当作尾部跳过。

## 门与射门复核

固定场外中线机位、镜头左右转动时，可以用已确认大门周边固定背景的特征匹配和单应变换推算门框。相机平移、变焦、场景改变或匹配失败时，回到未知并重新确认参考。当前包未内置该定位模型；需要时在任务工作目录实现，保存参考、匹配结果及失败区间。

判断射门需看连续动作：起脚/争顶、朝向、离脚后的球路、封堵、门将反应。门将扑地、球员在门前、音量大都只是辅助线索。球太小或被人遮挡时保留疑似，别硬报射手身份、球速或进球。

每个复核事件保存：

```json
{
  "source":"part1",
  "shot_seconds":325.5,
  "status":"confirmed",
  "description":"禁区前向门起脚，被防守队员封堵",
  "evidence":"focus_part1_0322_01.jpg",
  "clip_start":313.5,
  "clip_end":333.5
}
```

`shot_seconds` 是动作时间，不是片段开始。建议前12秒后8秒，边界截在本次比赛范围内；连续动作延长、重叠合并。不要把疑似事件放入主片然后称全部确认。音频 auto 仍带候选数量限制，完整射门复核须自行汇总全部事件并生成剪辑清单。

## 导出与验收

按事件从原片生成每个来源的 CSV（字段参照音频 scan 输出），可调用 `render`；多来源主片与疑似片分别编码再合并。复核动作表与所有原片偏移一并保留。CLI 的默认输出为宽度最高1280的音频初剪；要保留1080p和一致时基，采用以下已验证参数或在调用工具时提供相应滤镜：

```bash
ffmpeg -ss START -i SOURCE -t DURATION \
  -map 0:v:0 -map '0:a?' -vf fps=30 \
  -c:v libx264 -threads 4 -preset veryfast -crf 20 \
  -pix_fmt yuv420p -c:a aac -b:a 160k -ar 48000 \
  -video_track_timescale 15360 -movflags +faststart CLIP.mp4
```

START/SOURCE/DURATION/CLIP.mp4 替换为本次实际参数。跨来源合并先统一分辨率、帧率、像素格式、视频时基与音频参数；混有无声源时统一音轨结构。同参数片段可用 concat demuxer 合并。输出保存每段原文件名、源内起止、真实时钟（仅文件名确有起始时钟时）、合集内起止、判断与依据。

检查主片与疑似片完整解码，示例：

```bash
ffmpeg -hide_banner -loglevel error -xerror -threads 4 \
  -i highlights.mp4 -map 0:v:0 -map '0:a?' \
  -fps_mode:v passthrough -enc_time_base:v 1/15360 -f null -
```

确认退出成功、无解码错误、音画时长接近，抽看边界动作，核对排序、排除范围和索引链接。播放容器时长用于合并索引；AAC padding会带来毫秒差，长合集若要求精确逐帧跳转应以实际合并时间戳校准。

交付说明主片段数/时长、疑似段数/时长、复核覆盖范围。全场每2秒抽样加重点动作复核不能称为逐帧看完全场，也不能保证所有射门均被发现。
