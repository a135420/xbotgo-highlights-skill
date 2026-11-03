# 多原片任务工作台

`match_session.py` 管理任务与导出，不运行球门、球或事件识别模型。需要 Python 3.9+ 与 ffmpeg/ffprobe；独立的全时间轴抽图仍使用 `timeline_review.py` 和 Pillow。

## 建立任务与球门参考

工作目录放在仓库/skill之外。每个任务使用一个 session，同一时刻由一个进程修改。重复 init 同一组原片会保留已有台账；素材大小或修改时间变化会阻止继续，避免旧审核误用。该检查是文件元数据校验，不是逐字节内容校验。

以下命令在 skill 根目录执行，示例ID以实际 `status` 输出为准：

```bash
python3 scripts/match_session.py --session /path/to/task/session.json init /path/to/part1.mp4 /path/to/part2.mp4
python3 scripts/match_session.py --session /path/to/task/session.json status
python3 scripts/match_session.py --session /path/to/task/session.json frames --source F01 --at 30 60 90
```

先查看输出参考图，再手动标注比赛门。`--box` 为参考帧内归一化的 `x y width height`，范围0–1；A/B为固定的场地两端，独立于画面左右。每段原片、不同朝向或变焦后补充可见球门参考。球队与门将是参考时间处的说明，换边/换人后补参考，事件身份按事件发生时填写。

```bash
python3 scripts/match_session.py --session /path/to/task/session.json reference --source F01 --at 30 --end A --box 0.05 0.25 0.15 0.2 --label '树木一端比赛门' --team opponent
python3 scripts/match_session.py --session /path/to/task/session.json reference --source F01 --at 60 --end B --box 0.7 0.25 0.15 0.2 --label '建筑一端比赛门' --team ours --keeper '黄色上衣'
python3 scripts/match_session.py --session /path/to/task/session.json goals-page
```

将 `goal_review.html` 交给用户查看：支持拖拽重画门框、逐个确认/排除、填写说明、下载 `goal-review-result.json`。保留页面和 `frames/` 的相对位置，可本地打开或通过本地HTTP服务查看。导入用户实际审核的文件；agent不能代替用户点确认并声称已经由用户审核。

```bash
python3 scripts/match_session.py --session /path/to/task/session.json import-goals --file /path/to/goal-review-result.json
```

页面携带参考快照；参考变化后旧审核会被拒绝。审核期间登记查看进度不会使球门快照失效。修改已审核门框或撤销审核，会将关联的已确认事件退回疑似，需重新复核。页面截图或JSON无需上传外部服务。

**坐标仅在参考帧有效。** 当前未实现背景特征匹配或跨帧跟踪，不用同一个像素框裁切/筛除全片。新的镜头无法定位时仍保留候选，填 `--goal-ref unknown --status uncertain`，再补参考与复核。

## 一个事件台账，多种合集

从全时间轴找候选；重点连续画面判断后登记。动作时间不是片段起点，前后默认12/8秒，边界自动限于原片。类别支持 shot、goal、save、catch、claim、distribution、attack；门将站立接球、出击也可独立收录。

```bash
python3 scripts/match_session.py --session /path/to/task/session.json event --source F01 --at 65 --kind shot --status confirmed --goal-ref G002 --team opponent --reason '可见向门起脚，球被防守队员挡出' --goal-status not_goal
python3 scripts/match_session.py --session /path/to/task/session.json event --source F01 --at 85 --kind goal --status uncertain --goal-ref G002 --team opponent --reason '最后触球和门线被遮挡'
python3 scripts/match_session.py --session /path/to/task/session.json preview
```

`event_review.html` 提供可播放全幅短片、原速/慢放及按30fps步进。缓存文件由素材元数据、源内起止、参数和工具版本标识，修改事件边界会生成新短片，重复执行复用已有短片。编码先写临时文件，成功后才作为可复用缓存。该缓存不代表已完成内容审核。

修改时用同一 `--id` 更新全部事件字段，旧记录进入 history。确认进球须另外填写 `--goal-status confirmed --goal-evidence '实际球路/门线等证据'`；普通 confirmed 射门不会自动进入进球版。进球结果仍需 agent/用户查看画面判断，脚本只校验记录条件。

```bash
python3 scripts/match_session.py --session /path/to/task/session.json event --id E0002 --source F01 --at 85 --kind goal --status confirmed --goal-ref G002 --team opponent --reason '连续画面已复核' --goal-status confirmed --goal-evidence '球进入门框内，随后可见从球门内取球；中圈重开作佐证'
```

## 记录真实进度与导出

每查看一个范围后登记；`--note` 写抽样间隔、已看页码、重点复核材料等。重叠范围合并计算，不重复累计。包括赛前、暂停和尾段的抽样检查；排除比赛范围的依据另写在说明里，不把未看范围直接算已查。

```bash
python3 scripts/match_session.py --session /path/to/task/session.json coverage --source F01 --start 0 --end 96 --note '每2秒抽样，第1页已看；第65秒连续短片已复核'
python3 scripts/match_session.py --session /path/to/task/session.json status
python3 scripts/match_session.py --session /path/to/task/session.json export --collection goals --out-dir /path/to/task/goals-v1
python3 scripts/match_session.py --session /path/to/task/session.json export --collection goals --status uncertain --out-dir /path/to/task/goals-pending-v1
python3 scripts/match_session.py --session /path/to/task/session.json export --collection keeper --team ours --player '黄色门将' --out-dir /path/to/task/keeper-v1
```

默认所有原片已登记全时间轴复核才能导出。局部预览交付可以显式传 `--allow-partial`，输出会保留缺失范围的覆盖数据并标为 partial；它不是内容完整性验收。状态默认 confirmed，疑似需单独导出。`goals` 主片只选进球结果为 confirmed 的已确认事件；疑似进球版只选 kind=goal、status=uncertain、goal_status=unknown 的事件。

导出按素材顺序排列，合并同原片重叠片段，保留关联事件ID；不同原片不跨文件合并。统一1080P画布、30fps、AAC双声道，比例不同补边不裁画面，无音轨原片补静音以保持合并结构。输出主片、单段、`index.csv`、`export.json`，完整解码成功后才记录 decode_ok。输出目录需为空，重新导出选新版本目录，既有编码缓存仍可复用。

检查最终片段内是否完整包含起脚和后续；`index.csv` 合集时间按单段容器时长累计，AAC padding可能带来毫秒差，不能用于精确帧级定位。交付说明逐原片入选数量、疑似数量、复核覆盖和可能遗漏。
