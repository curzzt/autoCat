# autoCat 抖音自动拆片工具

输入一个抖音视频链接，自动完成下载、逐字稿转写、爆款分析与切片建议，并导出 Markdown、SRT 字幕与 Excel 拆片表。

设计方案见 [`docs/douyin-auto-slicing-design.md`](docs/douyin-auto-slicing-design.md)。

## 核心链路

```text
视频链接 → 下载视频 → ffmpeg 抽音频/关键帧 → ASR 转写 → OCR/视觉理解 → LLM 爆款分析与拆片 → 输出报告
```

各环节均做了优雅降级：链接下载失败可上传本地视频兜底；ASR/OCR/LLM 不可用时记录告警并基于已有数据继续生成报告。

## 环境依赖

运行时需要以下外部工具（缺失会在首页与告警中提示）：

- [`ffmpeg`](https://ffmpeg.org/) / `ffprobe`：抽取音频与关键帧
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)：视频下载
- 可选 ASR：`faster-whisper`
- 可选 OCR：`paddleocr`
- 可选 LLM：任意 OpenAI 兼容接口

## 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
# 可选能力
pip install -e ".[asr]"          # faster-whisper 转写
pip install -e ".[localmedia]"   # 无系统 ffmpeg 时，自带 ffmpeg 二进制
```

未检测到系统 `ffmpeg` 时，若安装了 `localmedia` 额外依赖，会自动回退使用 `imageio-ffmpeg` 内置的二进制（此回退仅提供 `ffmpeg`，不含 `ffprobe`，时长等信息可能显示「未获取」）。

## 配置

通过环境变量配置（均为可选，缺省走降级路径）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `AUTOCAT_LLM_BASE_URL` | OpenAI 兼容接口地址 | 空（关闭 LLM，启发式拆片）|
| `AUTOCAT_LLM_API_KEY` | LLM 密钥 | 空 |
| `AUTOCAT_LLM_MODEL` | 模型名 | `gpt-4o-mini` |
| `AUTOCAT_ASR_MODEL` | faster-whisper 模型 | `small` |
| `AUTOCAT_CLIP_MIN` / `AUTOCAT_CLIP_MAX` | 切片时长范围（秒）| `15` / `45` |
| `AUTOCAT_KEYFRAME_INTERVAL` | 关键帧抽取间隔（秒）| `5` |

## 使用

### 命令行（阶段 1）

```bash
python -m app.pipeline.run "https://www.douyin.com/video/..."
```

产物保存在 `storage/jobs/{job_id}/`，包含 `report.md`、`subtitle.srt`、`clips.xlsx` 等。

### Web 界面（阶段 2）

```bash
uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 ，输入链接后查看任务进度与拆片报告。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/jobs` | 用链接创建任务 |
| `POST` | `/api/jobs/upload` | 上传本地视频创建任务 |
| `GET` | `/api/jobs/{job_id}` | 查询任务状态与进度 |
| `GET` | `/api/jobs/{job_id}/result` | 获取分析结果 |
| `GET` | `/downloads/{job_id}/{filename}` | 下载产物文件 |

## 目录结构

```text
app/
  main.py          FastAPI 应用与路由
  config.py        配置与工具探测
  db.py            SQLite 持久层
  jobs.py          任务编排与状态机
  models.py        数据模型
  pipeline/        下载 / 媒体 / ASR / 视觉 / 分析 / 导出 / CLI
  templates/       首页 / 进度页 / 结果页
  static/          样式
storage/jobs/      任务产物
```
