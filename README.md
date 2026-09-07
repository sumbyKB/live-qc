# live-qc — 抖音 / TikTok 直播间录屏抽查与话术质检

一个面向 **AI Agent（Claude Code Skill）** 的直播间质检工具包：按账号名、品牌名、直播间链接或 TikTok 短链/@账号定位在播直播间 → 录屏转写 → 规则+语义双层级质检 → 输出飞书云文档报告。单间抽查与品牌矩阵批量巡检均支持，覆盖服装、软件/3C、食品、美妆、通用电商等多类目；抖音与 TikTok（海外）直播间均可，小语种（泰语等）话术走阿里云 ASR 秒级转写。

## 仓库内容

```
live-qc/
├── SKILL.md                      # Skill 主定义：完整工作流程、两个确认节点、批量场景、已知坑（AI 读这份执行）
├── scripts/
│   ├── search_live.py            # 按关键词搜索在播直播间；TikTok 支持 --mode users 发现品牌矩阵号（含未开播）
│   ├── probe_live.py             # 并发探测直播间开播状态（room_id / @账号 / 短链 / CSV 批量，双平台混测）
│   ├── record_live.py            # 提取直播流地址并 ffmpeg 录制（抖音走 CDP，TikTok 主路径 yt-dlp）
│   ├── batch_record.py           # 批量并行录制：每间独立 tab/子进程，落 manifest.json，可断点续跑
│   ├── qc_summary.py             # 汇总各房间扫描结果：风险分排序 + 立即整改清单 + 跨账号共性问题
│   ├── transcribe_aliyun.py      # 阿里云 DashScope ASR 快速转写（全语种10-20秒）；Key 存 local/aliyun_asr.key（gitignored）
│   ├── scan_violations.py        # 违禁词规则扫描 + SOP 覆盖自检（类目规则集，中/英文自动识别）
│   └── test_batch.py             # batch_record / qc_summary 纯逻辑自检（python3 test_batch.py）
├── references/
│   └── qc-criteria.md            # 质检维度与评分标准：通用九维度 + 类目适配表 + 综合评分算法
├── assets/
│   ├── report-template.xml       # 飞书云文档报告骨架（lark-cli docs +create 直接可用）
│   └── accounts.csv              # 已查过账号的 room_id/@handle 缓存（可选，非前置依赖）
├── start-debug-chrome.bat        # Windows 一键启动远程调试浏览器（端口 9222）
└── qc_runs/                      # 每次巡检的运行目录（gitignore），如 <日期_品牌>/{rooms.csv, rec/, scans/}
```

## 核心功能

| 功能 | 说明 | 入口 |
|------|------|------|
| **定位直播间** | 抖音 room_id 链接、TikTok 短链/`@账号`/裸用户名、关键词搜索直播；`--mode users` 按 TikTok 用户搜索发现品牌矩阵号（含未开播账号） | `search_live.py` |
| **开播探测** | 并发探测多个直播间是否在播，返回标题；支持 CSV 批量混测双平台 | `probe_live.py` |
| **单间录制** | 抖音经 Chrome CDP 提取 FLV 直录（按 `_or4 > _hd > _sd > _ld` 选最高清晰度）；TikTok 主路径 yt-dlp 无需登录，失败自动降级 CDP 兜底 | `record_live.py` |
| **批量录制** | 多间并行录制（默认 4 并发），每间独立浏览器 tab 互不干扰；产物 + `manifest.json` 集中在运行目录，中断后重跑自动跳过已完成房间 | `batch_record.py` |
| **逐字稿转写** | **已配阿里云 Key**：`transcribe_aliyun.py` 全语种（含泰语）10-20 秒出稿；**未配 Key**：飞书妙记 60-90 秒（中/英/菲语可用），小语种转不出时提示配置 Key，话术维度标注"不可评估" | `transcribe_aliyun.py` / 妙记 |
| **违禁词扫描** | 毫秒级正则规则层：绝对化用语、虚假宣传、医疗宣称、价格欺诈等，按高/中/低分级，每条带原话、上下文、法条依据和改写建议 | `scan_violations.py` |
| **类目与多语言规则** | `--category apparel/software/food/beauty/general` 激活类目专属规则与 SOP 检查项；中文走《广告法》规则，英文/泰文混合自动切 TikTok Shop 政策规则 | `scan_violations.py --category --lang` |
| **批量汇总** | 合并 manifest + 各房间扫描 JSON，输出风险分降序排序表（高危×10/中×3/低×1，≥15 标立即整改）、跨账号相同违规定位话术模板问题 | `qc_summary.py` |
| **语义质检与评分** | LLM 按通用九维度评分（合规性一票关键项），类目专属维度替换；覆盖语义型夸大、话术结构、互动质量等规则层抓不到的问题 | `references/qc-criteria.md` |
| **画面检查** | ffmpeg 抽帧（默认每 10 秒一帧）视觉巡检：贴片文字、价格标识、违规导流元素、主播形象；控制成本每间抽样最多 8 帧 | ffmpeg + AI 逐帧查看 |
| **飞书报告** | 按模板生成质检报告：基本信息、SOP 检查表、合规风险清单、亮点与改进建议（P0/P1/P2） | `assets/report-template.xml` + `lark-cli docs` |

## 工作流程（9 步 · 2 个确认节点）

单间抽查流程**不是一路跑到底**，在两个节点必须暂停等待用户确认：

```
定位直播间 → 确认在播 → 【节点一】确认抽查对象+录制时长(推荐2/3/5分钟)
  → 后台录制 → 转写(阿里云ASR秒级/妙记兜底) → 【节点二】交付MP4+逐字稿 → 确认质检范围
  → 质检(画面/话术合规/SOP/售后价格，默认全面质检) → 生成飞书报告 → 最终交付
```

- **节点一**：列出在播直播间候选，确认抽查哪个、录几分钟。
- **节点二**：素材先交付，再确认质检范围（画面检查 / 话术合规 / SOP执行 / 售后与价格 / 全面质检=默认）。

### 批量场景（矩阵巡检 / 全量检核 / 长时段录制）

三个高频场景复用同一条流水线，数据流为「文件即接口」（脚本间不互相 import）：

```
search_live.py(定位) → probe_live.py --file(探活) → batch_record.py(批量录制,落manifest.json)
  → 妙记逐个转写 → scan_violations.py --json(逐间扫描) → qc_summary.py(机械汇总排序) → AI 报告
```

- 每次巡检建运行目录 `qc_runs/<日期_品牌>/`：`rooms.csv`（`room,账号名,类目`，仅 LIVE 房间）、`rec/`（mp4 + manifest.json）、`scans/`、最终报告。
- **命名契约**：扫描结果必须存为 `scans/<mp4文件名>.json`，qc_summary 按此对账。
- **场景一（矩阵巡检）**：横向对比报告，重点写共性问题（统一话术模板嫌疑）与账号间差异；确认节点一合并为一次确认房间清单+时长。
- **场景二（全量检核）**：如 35 店全检，按风险分降序输出全量清单，风险分 ≥15 或命中高危标「⚠️ 立即整改」，报告开头给管理层摘要。
- **场景三（长时段录制）**：如录 10 分钟聚焦开场/上新/踢单报价环节，按逐字稿时间戳定位环节、对环节起止点补抽帧，报告按环节组织。

## 环境准备

| 依赖 | 用途 | 验证 |
|------|------|------|
| Chrome + 远程调试 | 直播间操作与取流 | `curl -s http://127.0.0.1:9222/json/list`；Windows 直接运行 `start-debug-chrome.bat` |
| 抖音登录态 | 存于调试浏览器 profile，未登录取流失败 | 首次在调试浏览器中手动登录 |
| TikTok 登录态 | 仅搜索和 CDP 兜底录制需要 | 在调试浏览器中登录 TikTok |
| ffmpeg | 录制与抽帧 | `ffmpeg -version` |
| Python 3 + websocket-client + requests | 全部脚本 | `python3 -c "import websocket, requests"` |
| yt-dlp | TikTok 直播间取流 | `yt-dlp --version` |
| TikTok 代理 | 国内网络直连不通 TikTok | 默认自动读系统代理，或设 `TIKTOK_PROXY=http://127.0.0.1:7897` |
| 阿里云 DashScope Key（可选，推荐） | 全语种秒级转写（泰语/菲语等妙记转不出的语言） | `local/aliyun_asr.key` 存入一行 sk- 开头的 Key（[百炼控制台创建](https://bailian.console.aliyun.com/?tab=model#/api-key)）；`python3 scripts/transcribe_aliyun.py --check` 验证。**该文件已被 .gitignore 忽略，绝不提交/上传**；未配置时流程自动回退妙记，不阻塞 |
| lark-cli | 妙记转写与飞书文档报告 | 已装 `doubao-video-extract` skill 的环境 |

## 在 AI 中使用

本仓库本身就是一个 **Claude Code Skill 包**（入口为 `SKILL.md`，frontmatter 含 `name: live-qc` 与触发描述）。

### 安装

```bash
# 方式一：全局安装（所有会话可用）
cp -r live-qc ~/.claude/skills/live-qc

# 方式二：项目级安装（仅当前项目可用）
cp -r live-qc <你的项目>/.claude/skills/live-qc
```

### 触发方式

安装后，AI 会根据 `SKILL.md` 的 description 自动匹配以下意图，也可显式调用：

- 「帮我抽查一下 XX 品牌的直播间」
- 「巡检/质检这个直播间：live.douyin.com/xxx」
- 「录制 TikTok 直播 @steapex.th 3 分钟」
- 「检查主播话术有没有绝对化用语 / 虚假宣传风险」
- 「查看某品牌矩阵号现在哪些在播」「把 XX 品牌 35 个店全检一遍出整改清单」
- 「生成这场的直播质检报告」

### AI 使用时的行为约定（写在 SKILL.md 中，Agent 会遵守）

1. **两个确认节点必须暂停等用户回复**，不会自动跑完全程（批量场景节点一合并为一次确认）。
2. 检测到用户意图后自动执行前置检查（调试端口、登录态、依赖、代理）。
3. 质检时先跑规则层（`scan_violations.py`），再做语义层评分；批量场景先 `qc_summary.py` 机械汇总，AI 只负责最终报告。
4. 妙记转写为空（小语种）时**不重录重传、不走本地 ASR**，提示用户配置阿里云 Key；未配置则该房间话术维度标注"不可评估"，画面维度照常质检。
5. 画面检查控制分析成本：每间抽样最多 8 帧，禁止逐张读完全部帧。
6. 报告必须包含妙记链接、质检范围，画面检查注明"基于抽帧、非逐帧审核"。

### 依赖的其他 AI 能力

- `doubao-video-extract` skill：录屏 MP4 上传妙记转写逐字稿。
- `lark-cli`：`docs +create` 生成报告、`vc +notes` 拉取逐字稿。
- `doubao-cron-scheduler` skill（可选）：常态化定时巡检。

## 手动使用（脱离 AI 直接跑脚本）

```bash
# 1. 关键词搜索在播直播间 / 发现品牌矩阵号
python3 scripts/search_live.py "某品牌官方旗舰店"                      # 抖音直播
python3 scripts/search_live.py "shoes" --platform tiktok              # TikTok 直播（需登录）
python3 scripts/search_live.py "steapex" --platform tiktok --mode users  # TikTok 用户搜索（含未开播）

# 2. 探测开播状态（可批量、可混平台）
python3 scripts/probe_live.py 641012837749
python3 scripts/probe_live.py @steapex.th "https://vt.tiktok.com/ZSxxxx/"
python3 scripts/probe_live.py --file assets/accounts.csv

# 3. 单间录制（后台运行，核验时长用 ffprobe）
python3 scripts/record_live.py --room 641012837749 --duration 300 --output brand_5min.mp4
python3 scripts/record_live.py --room @steapex.th --duration 180
python3 scripts/record_live.py --room @onke_th --use-cdp              # 强制走浏览器路径

# 4. 批量录制（并行 + 断点续跑：中断后原命令重跑，已 ok 房间自动跳过）
python3 scripts/batch_record.py --file qc_runs/run1/rooms.csv --duration 180 --outdir qc_runs/run1/rec

# 5. 逐字稿：已配阿里云 Key 走快速链路（全语种10-20秒）；未配则走妙记
python3 scripts/transcribe_aliyun.py rec/xxx_3min.mp4                 # 读 local/aliyun_asr.key，产物 xxx.aliyun.txt
python3 scripts/transcribe_aliyun.py --check                          # 检查 key 配置状态

# 6. 违禁词扫描（类目 + 语言自动识别；--json 结果按 <mp4名>.json 存入 scans/）
python3 scripts/scan_violations.py transcript.txt --category apparel
python3 scripts/scan_violations.py transcript.txt --category software --json
python3 scripts/scan_violations.py tiktok_transcript.txt              # 英文自动切 TikTok Shop 规则

# 7. 批量汇总（风险分排序 + 共性问题，输出 qc_summary.md/.json）
python3 scripts/qc_summary.py --manifest qc_runs/run1/rec/manifest.json --scans qc_runs/run1/scans

# 8. 自检（batch_record / qc_summary 纯逻辑，无需网络）
python3 scripts/test_batch.py

# 9. 抽帧做画面检查
ffmpeg -i brand_5min.mp4 -vf "fps=1/10,scale=1280:-1" -q:v 3 frames/frame_%03d.jpg
```

## 扩展与适配

- **新客户/新类目**：无需预置数据，给账号名直接搜索；按商品类目选 `--category`；有客户专属 SOP 时补充到 `references/qc-criteria.md` 的类目适配表。
- **新增违禁词**：在 `scripts/scan_violations.py` 的 `RULES`（中文）/ `EN_RULES`（英文）中追加规则元组 `(id, level, pattern, label, legal_basis, fix_hint, categories)`，`categories=None` 表示全类目生效。
- **定时巡检**：配合 `doubao-cron-scheduler` 建定时任务；注意登录态过期会导致取流失败，需加失败通知。
- **批量并发调整**：`batch_record.py --workers` 默认 4——全部走抖音 CDP 时共享一个调试浏览器，勿盲目调大。

## 关键限制

- `yt-dlp` **不支持**抖音直播（必须走 CDP）；但**支持** TikTok 直播（主取流路径）。
- TikTok 部分房间 webcast API 对未登录客户端返回空，此时自动降级浏览器 CDP（需调试浏览器已登录 TikTok）。
- ffmpeg 不走系统代理，TikTok 录制由脚本显式传 `-http_proxy`，录制期间代理必须在线。
- 流地址每场都变、签名约 1 小时过期，脚本实时取流，不做缓存复用。
- 违禁词命中 ≠ 必然违规、未命中 ≠ 合规——规则层结果需结合上下文人工复核（判定要点见 `references/qc-criteria.md`）。
- 画面检查基于抽帧，只能发现抽帧时刻的问题，报告中必须注明此局限。
- **飞书妙记对小语种覆盖弱**：泰语几乎无法转写（5 分钟仅出 1 句）、马来语仅零星覆盖、Taglish 尚可——小语种话术需配阿里云 Key 走快速链路；未配且妙记转不出时该房间话术维度标注"不可评估"，画面维度照常质检（本 skill 不内置本地 ASR）。
- 纯泰语话术规则层覆盖有限（泰语中的英文营销词可命中），外语逐字稿主要靠语义层（LLM）判读。
