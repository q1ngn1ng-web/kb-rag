# individual-knowledge-base

个人知识库工具链：文档 → Markdown → LightRAG 知识图谱 → MCP/CLI 查询。

## 环境

- Python 3.12，**必须用 uv** 管理依赖（不要用 pip install）
```bash
uv sync                     # 安装依赖
source .venv/bin/activate   # 激活虚拟环境
uv lock && uv sync          # 依赖变更后
```
> uv 默认镜像已配清华源（pyproject.toml `[[tool.uv.index]]`），新机器记得 `source ~/.bashrc` 加载 `UV_DEFAULT_INDEX` 环境变量。

### 敏感信息加载

API Key 三种来源（优先级从高到低）：

1. **shell 环境变量**（CI / Docker / 生产）
2. **`.env` 文件**（本地开发便捷）：`cp .env.example .env` 后填值
3. **`config.yaml`** 中 `api_key` 字段（兜底，不推荐存明文）

`config.py:load_config()` 会自动 `load_dotenv()`，无需手动 source。`.env` 已在 `.gitignore` 中。

## 关键命令

```bash
kb import <path>             # 文档 → Markdown（输出到 ./kb_data/）
kb import --index <path>     # 转换 + 索引到知识图谱
kb index <path>              # 已有 .md → 索引到知识图谱
kb serve                     # 启动 MCP Server (SSE, 0.0.0.0:8000)
kb ui                        # Streamlit 本地界面
kb query "问题"              # 命令行查询

python scripts/verify_llm.py # 验证 LLM + Embedding 连接
pytest tests/                # 运行测试
```

## 架构

```
src/knowledge_base/
  cli.py                    # CLI 入口，kb 命令分发
  config.py                 # load_config() — YAML + 环境变量覆盖
  importers/                # 文档导入（classifier → router → 各格式 importer）
  knowledge_graph/
    engine.py               # KnowledgeGraphEngine 抽象接口
    lightrag_engine.py       # LightRAG 实现（懒初始化，首次操作才加载）
  mcp_server/server.py      # FastMCP 服务器，4 个 tool
  obsidian.py               # Obsidian Vault 同步
```

### 三层管道

1. **导入**：文档 → 统一 Markdown（`kb import`）
2. **索引**：Markdown → LightRAG 知识图谱（`kb index`）
3. **查询**：MCP Server / CLI / Streamlit（`kb serve` / `kb query` / `kb ui`）

## 配置

- `config.yaml` — 主配置（已 gitignore 敏感信息）
- `config.local.yaml` — 本地覆盖（gitignore）
- 环境变量覆盖：`XIAOMI_API_KEY`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`MINERU_API_KEY`
- LLM 提供商：`xiaomi` | `deepseek` | `ollama`（config.yaml 中 `llm.provider`）
- Embedding 独立配置：`embedding` 节（默认 DashScope text-embedding-v4，维度 1024）

## 重要约定

- **不要直接调用 LightRAG API** — 所有操作通过 `KnowledgeGraphEngine` 抽象接口
- **配置不要硬编码** — 所有 URL、路径、API Key 从 config.yaml 或环境变量读取
- **PDF 路由**：启发式自动分类（公式密度、引用标记），普通文档走 PyMuPDF，论文/扫描件走 MinerU API
- **小米 provider**：有速率限制，LightRAG 并发已设为 4
- **MinerU v4 API**：只接受 URL 不接受文件上传。本地 PDF 需开放公网端口用 HTTP 提供文件，或在腾讯云/阿里云安全组放行端口后使用临时 HTTP 服务器
- **依赖声明**在 `pyproject.toml`，`[project.optional-dependencies]` 有 `test` 和 `ocr` 组
- **包入口**：`pyproject.toml` 的 `[project.scripts]` 定义 `kb` → `knowledge_base.cli:main`
- **包源码**在 `src/knowledge_base/`（setuptools `packages.find.where = ["src"]`）
- **OpenSpec 的 spec 文件必须全中文**（包括 requirement 描述、scenario 说明），代码注释和变量名保持英文

## Bug 记录

历史 Bug 和解决方式记录在 `docs/bugs.md`，遇到异常先查那里。

## OpenSpec 工作流

本仓库用 OpenSpec 管理变更，相关 skill 在 `.opencode/skills/openspec-*`。

| 命令 | 用途 |
|------|------|
| `/opsx:explore` | 探索想法、澄清需求 |
| `/opsx:propose` | 生成变更提案 |
| `/opsx:apply` | 按任务列表实现代码 |
| `/opsx:archive` | 归档已完成变更 |
| `/opsx:sync` | delta spec → main spec |

当前活跃变更：`openspec/changes/doc-to-knowledge-pipeline/`
