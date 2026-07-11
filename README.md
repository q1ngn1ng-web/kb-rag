# 个人知识库 (individual-knowledge-base)

将 PDF/Word/EPUB 等多格式文档转为 Markdown，通过 LLM 提取实体关系构建知识图谱，支持自然语言检索。

## 快速开始

```bash
# 1. 创建虚拟环境
uv python pin 3.12
uv sync
source .venv/bin/activate

# 2. 配置
cp config.yaml.example config.yaml  # 编辑 config.yaml
# 或用环境变量（推荐）：
export DEEPSEEK_API_KEY="sk-..."
export DASHSCOPE_API_KEY="sk-..."

# 3. 验证 LLM 连接
uv run python -m pytest tests/scripts/verify_llm.py -x -q

# 4. 转换文档为 Markdown（仅转换，不索引）
kb import ./my_books/

# 5. 索引到知识图谱
kb index ./kb_data/

# 快捷方式：转换 + 索引一步完成
kb import --index ./my_books/

# 6. 启动查询接口
kb serve        # MCP Server（供 LLM 客户端调用）
kb ui           # Streamlit 本地 UI
kb query "问题" # 命令行查询
```

## 架构（三层管道）

```
层1: 文档导入 → 统一 Markdown（kb import）
层2: Markdown → LightRAG 知识图谱（kb index）
层3: 查询接口（kb serve / kb ui / kb query）
```

### CLI 命令

| 命令 | 说明 |
|------|------|
| `kb import <path>` | 转换文档为 Markdown（仅转换） |
| `kb import --index <path>` | 转换 + 索引知识图谱 |
| `kb import --vault <path>` | 转换 + 同步到 Obsidian Vault |
| `kb index <path>` | 将 .md 文件索引到知识图谱 |
| `kb index --vault <path>` | 索引 + 同步实体笔记到 Obsidian |
| `kb serve` | 启动 MCP Server |
| `kb ui` | 启动 Streamlit 本地界面 |
| `kb query <question>` | 命令行查询 |

### 三种使用方式
- **MCP Server** — 主接口，让 Claude/Cursor 等 LLM 客户端直接查询知识库
- **Streamlit UI** — 本地浏览和调试
- **Obsidian Vault** — 按文档隔离输出 Markdown + 实体独立笔记 + [[wikilinks]]

## 文档导入支持

| 格式 | 引擎 | 说明 |
|------|------|------|
| PDF (普通) | PyMuPDF | 0.01s/页，免费，保留文本/段落/基础表格 |
| PDF (论文) | MinerU API | 公式→LaTeX，表格→HTML，图表→图片 |
| PDF (扫描件) | 自动升级→MinerU | PyMuPDF 检测无文本层时自动切换 |
| DOCX | python-docx | 标题/列表/表格/图片 |
| EPUB | ebooklib | 章节/图片/元数据 |
| TXT | 原生 | 自动编码检测 (UTF-8/GBK) |
| HTML | BeautifulSoup | 标题/链接/列表/图片 |

## 配置

参考 `config.yaml.example`，复制为 `config.yaml` 后按需修改：

```bash
cp config.yaml.example config.yaml
```

关键配置项：

| 配置段 | 说明 |
|--------|------|
| `llm` | LLM 提供商（DeepSeek / Ollama），需填写 `api_key` 或设置环境变量 |
| `embedding` | embedding 模型（默认阿里云 DashScope text-embedding-v4） |
| `pdf.routing` | PDF 自动路由策略（普通 PyMuPDF / 论文 MinerU） |
| `mineru` | MinerU OCR API 配置 |
| `knowledge_graph` | LightRAG 参数（分块、检索、超时） |
| `obsidian` | Obsidian Vault 路径（配置后 `--vault` 自动同步） |

### 环境变量

敏感信息优先从环境变量读取（config.yaml 中 `api_key` 可留空）：

| 环境变量 | 用途 | 获取方式 |
|----------|------|----------|
| `DEEPSEEK_API_KEY` | LLM 调用（DeepSeek 提供商） | [DeepSeek 平台](https://platform.deepseek.com) |
| `DASHSCOPE_API_KEY` | Embedding 向量（阿里云 DashScope） | [阿里云 Model Studio](https://model-studio.aliyun.com) |
| `MINERU_API_KEY` | PDF OCR（论文/扫描件） | [MinerU 平台](https://mineru.net) |

## MCP 配置 (供 Claude/Cursor 使用)

将以下配置添加到 LLM 客户端的 MCP 设置中：

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/individual-knowledge-base", "kb", "serve"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-..."
      }
    }
  }
}
```

## 开发

```bash
uv sync                # 安装依赖
source .venv/bin/activate
pytest tests/           # 运行测试
```
