# 抖音自动拆片工具设计方案

## 1. 目标

本项目目标是实现一个“只输入抖音视频链接，就自动生成拆片报告”的工具。

用户输入：

```text
抖音视频链接
```

系统输出：

1. 视频基础信息：标题、作者、时长、点赞、评论、分享、收藏。
2. 逐字稿：带时间轴的分段文稿。
3. 爆款分析：开头钩子、情绪曲线、冲突点、信息密度、信任背书、转发理由。
4. 自动拆片表：每条切片的开始时间、结束时间、标题、爆点、适合平台、剪辑建议。
5. 可选导出：Markdown、Excel、SRT 字幕、剪映草稿。

核心判断：纯提示词不能稳定完成“只给链接就出结果”，必须配合下载器、`ffmpeg`、ASR、OCR/视觉理解和 LLM 分析链路。

## 2. 产品形态

MVP 采用极简形态：

```text
一个输入框 + 一个任务进度页 + 一个结果页
```

页面流程：

1. 首页输入抖音链接。
2. 点击“开始分析”后创建任务。
3. 展示任务进度。
4. 分析完成后展示报告。
5. 支持下载 Markdown、Excel、SRT。

建议保留本地视频上传入口作为兜底能力。抖音链接解析和下载容易受平台反爬、登录态、链接格式变化影响，当链接下载失败时，用户仍可上传视频继续完成转写和分析。

## 3. 最低可用链路

```text
视频链接
→ 下载视频
→ ffmpeg 提取音频和关键帧
→ ASR 转写文稿
→ OCR/视觉模型理解画面
→ LLM 做爆款分析和拆片
→ 输出拆片表和报告
```

MVP 可以先弱化视觉理解，只做关键帧抽取和基础画面描述；当 OCR/视觉模型失败时，基于文稿继续生成拆片报告。

## 4. 技术选型

| 模块 | MVP 方案 | 后续增强 |
|---|---|---|
| Web 框架 | FastAPI + Jinja2/HTMX | Next.js + FastAPI |
| 后端任务 | FastAPI BackgroundTasks | Redis + RQ/Celery |
| 数据库 | SQLite | PostgreSQL |
| 文件存储 | 本地 `storage/jobs/{job_id}` | S3/OSS/MinIO |
| 视频下载 | `yt-dlp` 适配层 | Playwright 登录态抓取、自建解析服务 |
| 音视频处理 | `ffmpeg` / `ffprobe` | 场景检测、镜头分割 |
| ASR | FunASR 或 Whisper | 云 ASR 多供应商兜底 |
| OCR | PaddleOCR | 多模态视觉模型 |
| LLM | OpenAI 兼容接口 | 多模型路由、成本控制 |
| 导出 | Markdown / SRT / XLSX | 剪映草稿 |

## 5. 系统架构

```text
┌────────────┐
│ Web 前端   │
│ 输入链接   │
└─────┬──────┘
      │
      ▼
┌────────────┐
│ FastAPI    │
│ 创建任务   │
└─────┬──────┘
      │
      ▼
┌────────────┐
│ Job Runner │
│ 流水线编排 │
└─────┬──────┘
      │
      ├── 下载视频
      ├── ffmpeg 抽音频/关键帧
      ├── ASR 转写
      ├── OCR/视觉分析
      ├── LLM 爆款分析
      └── 导出文件
      │
      ▼
┌────────────┐
│ 结果页     │
│ 报告/表格  │
└────────────┘
```

## 6. 项目目录建议

```text
autoCat/
  app/
    main.py
    config.py
    db.py
    jobs.py
    models.py
    pipeline/
      __init__.py
      ingest.py
      media.py
      asr.py
      vision.py
      analyze.py
      export.py
    templates/
      index.html
      job.html
      result.html
    static/
      app.css
  docs/
    douyin-auto-slicing-design.md
  storage/
    jobs/
  tests/
  pyproject.toml
  README.md
```

## 7. 核心模块设计

### 7.1 链接解析与视频下载

职责：

1. 接收抖音分享链接。
2. 标准化 URL。
3. 尝试解析真实视频地址。
4. 下载视频到任务目录。
5. 提取可获得的基础信息。

输出文件：

```text
storage/jobs/{job_id}/input.json
storage/jobs/{job_id}/video.mp4
storage/jobs/{job_id}/metadata.json
```

注意事项：

1. 抖音下载链路不稳定，需要设计为可替换适配器。
2. 元数据无法获取时不阻断任务，字段标记为 `null` 或“未获取”。
3. 需要记录失败原因，方便结果页展示。

### 7.2 音视频处理

职责：

1. 使用 `ffprobe` 获取时长、分辨率、编码等信息。
2. 使用 `ffmpeg` 抽取音频。
3. 按固定间隔或场景变化抽取关键帧。

推荐命令：

```bash
ffmpeg -y -i video.mp4 -vn -ac 1 -ar 16000 audio.wav
ffmpeg -y -i video.mp4 -vf fps=1/5 frames/frame_%04d.jpg
ffprobe -v quiet -print_format json -show_format -show_streams video.mp4
```

输出文件：

```text
audio.wav
frames/frame_0001.jpg
media_info.json
```

### 7.3 ASR 转写

职责：

1. 将音频转为带时间轴的逐字稿。
2. 输出结构化 JSON。
3. 生成 SRT 字幕。

输出示例：

```json
[
  {
    "start": 0.0,
    "end": 3.2,
    "text": "今天这个视频我一定要讲清楚。"
  },
  {
    "start": 3.2,
    "end": 7.8,
    "text": "为什么很多人剪视频没有流量？"
  }
]
```

输出文件：

```text
transcript.json
subtitle.srt
```

### 7.4 OCR 与视觉理解

MVP 能力：

1. 对关键帧做 OCR，提取屏幕文字。
2. 生成简单画面描述。
3. 标记画面变化点。

输出示例：

```json
[
  {
    "time": 5.0,
    "frame": "frames/frame_0001.jpg",
    "ocr_text": "普通人做账号最容易犯的 3 个错",
    "description": "人物口播，画面上方有大字标题。"
  }
]
```

可选增强：

1. 识别人物、商品、场景、截图、聊天记录、演示画面。
2. 根据画面变化辅助切片边界。
3. 提取封面候选帧。

### 7.5 LLM 分析

LLM 输入：

1. 视频基础信息。
2. 带时间轴逐字稿。
3. OCR/画面描述。
4. 任务参数，例如目标平台、切片时长范围。

LLM 输出：

1. 爆款拆解报告。
2. 视频结构拆解。
3. 自动拆片表。
4. 标题和封面文案建议。
5. 二创改写方向。
6. 风险提示。

建议将 LLM 输出要求为 JSON，再渲染成 Markdown 和结果页，避免直接解析自由文本表格。

## 8. 数据模型

### 8.1 Job

```json
{
  "id": "job_20260615_001",
  "input_url": "https://www.douyin.com/...",
  "status": "completed",
  "created_at": "2026-06-15T10:00:00+08:00",
  "updated_at": "2026-06-15T10:05:00+08:00",
  "error": null
}
```

### 8.2 VideoMetadata

```json
{
  "url": "https://www.douyin.com/...",
  "title": "视频标题",
  "author": "作者名称",
  "duration": 68.5,
  "likes": 12000,
  "comments": 860,
  "shares": 300,
  "favorites": 1500
}
```

### 8.3 TranscriptSegment

```json
{
  "start": 12.4,
  "end": 18.9,
  "text": "这个开头的关键不是炫技，而是先制造一个反差。"
}
```

### 8.4 ClipSuggestion

```json
{
  "index": 1,
  "start": 12.4,
  "end": 38.6,
  "title": "普通人做短视频最容易踩的坑",
  "hook": "你的视频没人看，可能不是内容差。",
  "cover_text": "流量差的真正原因",
  "hotspot_type": "反差观点",
  "reason": "开头有问题识别，中段给出原因，结尾有可执行建议。",
  "edit_suggestion": "前 2 秒放大字幕，保留停顿，结尾加总结卡片。",
  "platforms": ["抖音", "视频号", "小红书"],
  "potential": "高"
}
```

## 9. 任务状态机

```text
pending
→ downloading
→ processing_media
→ transcribing
→ analyzing_visuals
→ generating_report
→ exporting
→ completed
```

失败状态：

```text
failed
```

部分失败不一定进入 `failed`。例如评论数未获取、OCR 失败、视觉描述失败，都可以记录 warning，并继续生成报告。

```json
{
  "warnings": [
    "未能获取评论数",
    "OCR 失败，已基于逐字稿继续分析"
  ]
}
```

## 10. API 设计

### 10.1 创建任务

```http
POST /api/jobs
Content-Type: application/json

{
  "url": "https://www.douyin.com/..."
}
```

响应：

```json
{
  "job_id": "job_20260615_001",
  "status": "pending"
}
```

### 10.2 查询任务状态

```http
GET /api/jobs/{job_id}
```

响应：

```json
{
  "job_id": "job_20260615_001",
  "status": "transcribing",
  "progress": 55,
  "message": "正在生成逐字稿"
}
```

### 10.3 获取结果

```http
GET /api/jobs/{job_id}/result
```

响应：

```json
{
  "metadata": {},
  "transcript": [],
  "analysis": {},
  "clips": [],
  "exports": {
    "markdown": "/downloads/job_20260615_001/report.md",
    "xlsx": "/downloads/job_20260615_001/clips.xlsx",
    "srt": "/downloads/job_20260615_001/subtitle.srt"
  }
}
```

## 11. 结果页结构

结果页建议分为 7 个区域：

1. 基础信息卡片
2. 逐字稿时间轴
3. 视频结构拆解
4. 爆款原因分析
5. 自动拆片表
6. 标题/封面文案建议
7. 导出按钮

自动拆片表字段：

| 序号 | 开始时间 | 结束时间 | 切片标题 | 爆点类型 | 推荐理由 | 开头字幕 | 封面文案 | 剪辑建议 | 适合平台 | 预估传播潜力 |
|---|---|---|---|---|---|---|---|---|---|---|

## 12. LLM 提示词策略

建议拆成两个阶段，避免单次提示词过长且难以稳定输出。

### 12.1 爆款分析提示词

输入：

1. 视频基础信息。
2. 带时间轴逐字稿。
3. 关键帧 OCR 和画面描述。

输出：

1. 开头 3 秒钩子。
2. 核心冲突/悬念。
3. 情绪价值。
4. 信息价值。
5. 信任背书。
6. 转发理由。
7. 评论区触发点。
8. 内容结构拆解。
9. 风险点。

### 12.2 拆片提示词

约束：

1. 每条切片控制在 15-45 秒。
2. 优先选择强钩子、强情绪、强反差、强干货、强争议、强结果展示片段。
3. 每条切片必须能独立成立。
4. 必须给出标题、封面文案、开头字幕、剪辑建议。
5. 如果不适合拆片，需要明确说明原因。

建议输出 JSON：

```json
{
  "clips": [
    {
      "start": 0,
      "end": 30,
      "title": "",
      "hotspot_type": "",
      "reason": "",
      "opening_subtitle": "",
      "cover_text": "",
      "edit_suggestion": "",
      "platforms": [],
      "potential": ""
    }
  ]
}
```

## 13. 导出设计

### 13.1 Markdown

文件：

```text
report.md
```

包含完整报告，适合复制到 Notion、飞书、公众号或交给团队协作。

### 13.2 Excel

文件：

```text
clips.xlsx
```

Sheet 建议：

1. 基础信息
2. 逐字稿
3. 自动拆片表
4. 标题文案
5. 风险提示

### 13.3 SRT

文件：

```text
subtitle.srt
```

用于剪辑软件导入字幕。

### 13.4 剪映草稿

剪映草稿建议放到后续阶段实现。原因是草稿格式和版本变化较快，MVP 先输出标准字幕和切片表更稳定。

## 14. MVP 开发计划

### 阶段 1：命令行流水线

目标：先跑通核心能力，不做复杂页面。

命令示例：

```bash
python -m app.pipeline.run "https://www.douyin.com/..."
```

交付：

1. 下载视频。
2. 抽取音频。
3. ASR 转写。
4. 生成 Markdown 报告。

### 阶段 2：Web 输入框和结果页

目标：实现用户可用的最小产品。

交付：

1. 首页输入链接。
2. 创建分析任务。
3. 任务进度展示。
4. 结果页展示。
5. Markdown/SRT/XLSX 下载。

### 阶段 3：视觉增强

目标：提高拆片准确率。

交付：

1. 关键帧 OCR。
2. 画面描述。
3. 封面候选帧。
4. 基于画面变化优化切片边界。

### 阶段 4：生产化

目标：提升稳定性和可运维性。

交付：

1. Redis 队列。
2. 任务重试。
3. 登录态管理。
4. 成本统计。
5. 模型供应商配置。
6. 用户历史任务。

## 15. 风险与处理策略

| 风险 | 影响 | 策略 |
|---|---|---|
| 抖音链接下载失败 | 无法自动获取视频 | 支持上传本地视频兜底 |
| 平台反爬变化 | 下载模块失效 | 下载器做适配层，可替换 |
| ASR 错字 | 影响分析质量 | 支持用户编辑逐字稿后重新分析 |
| 视频太长 | 成本高、上下文超限 | 分段转写、分段摘要、再汇总 |
| LLM 输出不稳定 | 表格字段缺失 | 要求 JSON 输出并做 schema 校验 |
| OCR/视觉失败 | 画面分析缺失 | 降级为文稿分析 |
| 版权和合规 | 二创风险 | 输出版权、肖像、医疗、金融等风险提示 |

## 16. 推荐默认配置

```yaml
clip:
  min_seconds: 15
  max_seconds: 45
  target_platforms:
    - douyin
    - xiaohongshu
    - shipinhao

media:
  audio_sample_rate: 16000
  keyframe_interval_seconds: 5

analysis:
  language: zh-CN
  output_format: json
  continue_on_partial_failure: true
```

## 17. 第一版验收标准

第一版完成后，应满足：

1. 输入一个可下载的视频链接，能生成完整 Markdown 报告。
2. 报告包含基础信息、逐字稿、爆款分析和拆片表。
3. 能导出 SRT 字幕。
4. 能导出 Excel 拆片表。
5. 下载失败时，有清晰错误提示。
6. OCR 或评论数据获取失败时，不阻断主流程。
7. 所有任务产物按 `storage/jobs/{job_id}` 保存，便于排查和复用。

## 18. 后续可扩展能力

1. 评论区高频词分析。
2. 同类爆款对标分析。
3. 自动生成二创脚本。
4. 自动生成标题 A/B 测试版本。
5. 自动挑选封面帧。
6. 自动生成切片预览视频。
7. 剪映草稿导出。
8. 多账号、多项目管理。
9. 成本统计和模型路由。
10. 团队协作和报告分享链接。
