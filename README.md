# 多模型对比工具

同时向多个模型提问，对比回复差异。

## 功能

- 多模型并发对比：同时向 DeepSeek、千问、豆包、GLM 等提问
- 差异汇总：另请一个未参赛（尽量不同厂商）的模型做匿名评卷
- 模型在线配置：页面上改 base_url / model / API Key / 协议 / 启用开关，写入本地库
- 连通性测试：每个模型发一次最小请求，回显状态码与耗时，或原始错误
- 导出：当前对比导出 Markdown / JSON / CSV；历史记录可全量导出 CSV
- 自定义模型：可添加任意 OpenAI 兼容 API
- 历史记录：保存对比结果，随时回溯

## 快速启动

```bash
# 1. 配置 API Key
cd backend
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 2. 一键启动
cd ..
chmod +x start.sh
./start.sh
```

## 手动启动

```bash
# 后端
cd backend
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
python app.py  # 端口 6364

# 前端
cd frontend
npm install
npm run dev  # 端口 6363
```

## 支持的模型

| 模型 | API Key 环境变量 |
|------|-----------------|
| DeepSeek V4 Pro | DEEPSEEK_API_KEY |
| 通义千问 Plus | DASHSCOPE_API_KEY |
| 通义千问 Max | DASHSCOPE_API_KEY |
| 通义千问 Turbo | DASHSCOPE_API_KEY |
| 豆包 | DOUBAO_API_KEY |
| 智谱 GLM-4 | ZHIPU_API_KEY |
| 腾讯元宝 | HUNYUAN_API_KEY |
| 任意 OpenAI 兼容 API | 通过「模型管理」添加 |

## 模型与 Key 的配置方式

两种方式，页面配置优先：

1. 页面上改：「模型管理」里每个模型有编辑 / 测试 / 重置。改动写入本地 SQLite 覆盖层，重启不丢。
   - API Key 框留空 = 保留原值；点重置 = 丢弃覆盖，回到代码或 `.env` 的默认配置。
   - 接口只返回掩码（如 `sk-xxxx****xxxx`）和来源（`db` / `env`），不回传明文 Key。
2. `.env`：变量名见上表，`backend/.env` 与表格一致。

Key 读取顺序：页面/DB 覆盖 → `backend/.env` → 进程环境变量 → 空（报未配置 API Key）。`ALIBABA_API_KEY`、`VOLCENGINE_API_KEY` 仅在前几步都空时作别名兜底。请用 `./start.sh` 启动，以便加载 `.env`。

## 使用注意

- 豆包请填模型名（如 `doubao-1-5-pro-32k-250115`），不要填已删除的 `ep-xxxx` 推理接入点。
- 千问请用 `https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容）。
- 改完在页面上点「测试」即可确认是否连通。某家限流（429）时，差异汇总会自动换评委；该家自己的卡片仍会显示错误。

## 技术栈

- 后端：FastAPI + asyncio + SQLite
- 前端：Vue 3 + Ant Design Vue + Vite
- 端口：前端 6363 / 后端 6364

## API 文档

启动后端后访问 http://127.0.0.1:6364/docs
