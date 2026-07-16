# Bug 记录

> 项目开发过程中遇到的问题及解决方案汇总。

---

## 1. `delete_document` 调用异步方法名错误

- **发现时间**：2026-07-04（Phase 2 重索引时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：`kb index --force` 删除旧文档时提示"当前 LightRAG 版本不支持文档删除"
- **产生原因**：代码调用 `self._rag.delete_by_doc_id(doc_id)`，但 LightRAG 只有 `adelete_by_doc_id`（异步版本）。`AttributeError` 被 `except AttributeError` 捕获，错误地判定为"版本不支持"
- **解决方案**：将 `delete_by_doc_id` 改为 `adelete_by_doc_id`，用 `asyncio.run()` 包装
- **成效**：`--force` 标志正常工作，旧数据可清理后重索引

---

## 2. 语义缓存阈值过高，同义问题无法命中

- **发现时间**：2026-07-04（语义缓存验证时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**："六种理分别是什么"和"六种理各有什么特点"余弦相似度 0.9438，低于默认阈值 0.95，无法命中缓存
- **产生原因**：DashScope text-embedding-v4 对不同表述的嵌入向量差异比预期大，保守阈值 0.95 针对性不够
- **解决方案**：调低默认阈值至 0.92
- **成效**：同义问题正常命中缓存，响应时间从 ~20s 降至 ~3s

---

## 3. R 策略分块缺少 `langchain-text-splitters` 依赖

- **发现时间**：2026-07-04（Phase 2 重索引时）
- **涉及文件**：`pyproject.toml`
- **问题现象**：LightRAG 使用 `process_options="R"` 时抛出 `ImportError: langchain-text-splitters is required for the 'R' chunking strategy`
- **产生原因**：R 策略（递归字符分割）依赖 LangChain 的文本分割器，但未声明在依赖中
- **解决方案**：`uv add langchain-text-splitters`
- **成效**：R 策略分块成功，17 个语义完整的块

---

## 4. uv 依赖安装超时

- **发现时间**：2026-07-04（安装 `langchain-text-splitters` 时）
- **涉及文件**：`~/.bashrc`、`pyproject.toml`
- **问题现象**：`uv add` 下载超时（120s），无法安装依赖
- **产生原因**：默认 PyPI 源在国内访问慢
- **解决方案**：配置清华镜像源（`~/.bashrc` 加 `UV_DEFAULT_INDEX`，`pyproject.toml` 配 `[[tool.uv.index]]`）
- **成效**：安装时间从 120s+ 降至 ~10s

---

## 5. BM25 独立注入引入噪音，LLM 产生幻觉

- **发现时间**：2026-07-06（BM25 多路召回验证时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：BM25 注入后 LLM 称"唐代思想家唐君毅"（唐君毅是现代人），回答质量明显下降
- **产生原因**：BM25 按关键词匹配返回的块包含"理"但不涉及"六种理"（如孟子性善论、戴东原哲学），作为独立区块注入 prompt 后 LLM 被干扰
- **解决方案**：改为只做 RRF 重排序（不引入新块），BM25 分数只调整已有向量块的顺序
- **成效**：回答质量恢复甚至超过原有水平，子要点和术语引用更丰富

---

## 6. BM25 内容匹配逻辑错误

- **发现时间**：2026-07-06（BM25 重排序排查时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：`_rerank_chunks_with_bm25` 中所有 entry 的 BM25 分数恒为 0，RRF 未生效
- **产生原因**：匹配逻辑用 `cid in content_preview` 检查 chunk_id 是否在 content 前 50 字符中。但 chunk_id 格式为 `《中国哲学原论》前六章-chunk-006`，content 是纯文本（如 `自序 ...`），两者无交集，永远匹配不上
- **解决方案**：改为通过 `kv_store_text_chunks.json` 构建 `content[:60] → chunk_id` 映射
- **成效**：BM25 分数正确匹配到对应块，RRF 正常生效

---

## 7. jieba 懒加载位于查询路径上导致卡顿

- **发现时间**：2026-07-06（用户反馈查询卡住时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：首次查询时 jieba 加载词典（~0.7s），位于 LightRAG 构建 prompt 之后、LLM 生成之前的关键路径上
- **产生原因**：jieba 默认懒加载，第一次 `cut()` 调用时加载词典。BM25 RRF 重排序在查询流程中才首次调用 jieba
- **解决方案**：在 `LightRAGEngine.__init__()` 中预加载 jieba（调用 `jieba.cut("")`）
- **成效**：词典加载移至引擎初始化阶段，查询路径不受影响

---

## 9. LLM 调用无超时保护，API 挂起时查询卡死

- **发现时间**：2026-07-07（查询"王阳明知行合一"时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：查询执行后终端无输出，用户感知为"卡住了"——实际是 LLM API 调用在等待响应，但无任何进度提示
- **产生原因**：两阶段查询中阶段 3 自行调用 `asyncio.run(self._llm_func(...))`，AsyncOpenAI 默认超时为 600 秒，且无日志提示
- **解决方案**：用 `asyncio.wait_for()` 包装 LLM 调用，默认 120 秒超时；超时前打印"LLM 生成回答中（最长等待 N 秒）…"日志；`llm.timeout` 可在 config.yaml 中配置
- **成效**：LLM API 挂起时 120 秒后抛出明确超时异常，用户能感知进度

---

## 7. jieba 懒加载位于查询路径上导致卡顿

- **发现时间**：2026-07-06（排查查询卡死时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：BM25 重排序中的任何异常（如匹配失败、索引错误）会冒泡到 `query()` 的 `try` 块，可能导致流程卡住
- **产生原因**：`_rerank_chunks_with_bm25` 没有内部异常捕获，依赖外层 `try/except` 兜底
- **解决方案**：整个方法用 `try/except` 包裹，失败时 log warning 并返回原始 prompt
- **成效**：BM25 重排序失败不会影响查询流程，用户无感知

---

## 10. Streamlit UI 切换页面时 asyncio.Lock 跨事件循环失效

- **发现时间**：2026-07-11（Streamlit UI 多页面切换时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：Streamlit UI 中切换页面（如从"文档导入"切到"知识检索"），后台报错 `RuntimeError: <asyncio.locks.Lock object ...> is bound to a different event loop`，知识图谱查询/操作全部失败
- **产生原因**：
  - `_ensure_initialized()` 中 `asyncio.run(self._rag.initialize_storages())` 创建**临时事件循环**并关闭
  - LightRAG 内部 `shared_storage.py` 的模块级 `asyncio.Lock`（`_data_init_lock`、`_internal_lock`）在首次 `await` 时绑定到该临时循环
  - 后续 LightRAG 的同步包装器（`query()`、`insert()` 等）调用 `_run_sync()` → `always_get_an_event_loop()` 使用**线程持久的另一个事件循环**
  - 锁绑在已关闭的临时循环上，新循环无法 acquire → RuntimeError
  - 雪上加霜：如果初始化因锁错误失败，`streamlit` 不缓存引擎，下次 rerun 重建引擎；但 LightRAG 模块级 `_initialized` 标志已为 `True`，`initialize_share_data()` 跳过锁创建，旧锁继续导致失败 → 死循环
- **解决方案**（两步）：
  1. **初始化前**调 `finalize_share_data()`：重置 `_initialized = None`，让 LightRAG 在当前事件循环重新创建锁和共享字典
  2. **初始化后**替换 `_data_init_lock` 和 `_internal_lock`：`asyncio.run()` 结束后临时循环关闭，新的锁在首次 `await` 时绑定到持久事件循环
- **为什么保留 `@st.cache_resource`**：引擎只创建一次，后续 rerun 跳过 `_ensure_initialized()`，直接使用步骤 2 替换后的锁（已绑定到持久循环）
- **为什么不改 LightRAG 源码**：修改 `shared_storage.py` 使用 `threading.Lock` 会侵入上游。当前方案调用 `finalize_share_data()` 是其自有 API，替换两个锁变量也容易适配版本变更
- **成效**：多页面切换正常，知识图谱查询/索引/删除操作跨页面稳定可用

---

## 11. `list_documents()` 读取错误的文件路径，始终返回空列表

- **发现时间**：2026-07-11（UI 切换页面修复后验证时）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：UI 中文档列表页始终显示"尚未导入任何文档"，即使已成功导入多个文档
- **产生原因**：`list_documents()` 尝试读取 `working_dir/documents/` 目录（LightRAG v1.5+ 不创建此目录），然后回退到 `working_dir/doc*.json` 文件（LightRAG 的 KV 文件命名是 `kv_store_*.json`，不匹配 `doc*.json` 模式）。两种路径都失败，返回空列表
- **解决方案**：改为直接读取 `kv_store_full_docs.json` 文件，解析其中的 `doc_id → info` 映射
- **成效**：文档列表正确显示所有已索引文档

---

## 12. `chunk_size=8192` 过大，导致 LLM 生成缓慢 + prompt 超长

- **发现时间**：2026-07-16（用户反馈查询 40.8s 耗时过高时）
- **涉及文件**：`config.yaml`、`src/knowledge_base/knowledge_graph/lightrag_engine.py`
- **问题现象**：单次查询总耗时 ~40 秒，其中 LLM 调用占 21+ 秒；LLM 返回内容仍偶有"幻觉"，上下文感觉很满
- **产生原因**：分块尺寸 `chunk_size=8192` token，即使 `chunk_top_k=15` 只取 15 个块，单 prompt 仍可达 12 万+ token；DeepSeek 对超长上下文首 token 生成非常慢
- **解决方案**：
  1. `config.yaml`：
     ```yaml
     knowledge_graph:
       chunk_size: 8192 → 4096
       chunk_overlap: 512 → 256
       top_k: 40 → 35
       chunk_top_k: 15 → 12
       max_entity_tokens: 6000 → 5000
       max_relation_tokens: 8000 → 7000
       max_total_tokens: 50000 → 35000
     ```
  2. 配套调小 `llm.timeout: 300 → 120`、`query_timeout: 60 → 90`
  3. **必须 `kb index --force` 重建索引**：chunk_size 是索引时参数，对已索引文档不生效
- **成效**：
  - 重建后 4 篇文档 chunk 数 73 → 142（更细粒度）
  - 单次查询 28.71s → 17.06s（未重建，仅改查询参数）→ **10.5~12s**（重建后）
  - LLM 调用从 21s → ~10s

---

## 13. LightRAG `RoleLLMConfig` API 误用，抽取阶段走默认模型导致格式错误频发

- **发现时间**：2026-07-16（重建索引时观察到大量 `Complete delimiter can not be found` / `LLM output format error` 警告）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`、`config.yaml`
- **问题现象**：索引日志中频繁出现：
  ```
  WARNING: ...-chunk-000: Complete delimiter can not be found in extraction result
  WARNING: ...-chunk-007: LLM output format error; found 4/5 fields on RELATION ...
  ```
  部分 chunk 抽取失败，实体/关系被丢弃
- **产生原因**：
  1. 默认 `model: "deepseek-v4-flash"` 在结构化抽取任务上格式遵循能力较差
  2. 抽取阶段没有独立模型，复用查询阶段 LLM（temperature=0.7 偏高，不适合结构化输出）
- **解决方案**：
  1. `config.yaml` 新增抽取阶段独立配置：
     ```yaml
     llm:
       extract:
         model: "deepseek-chat"   # 结构化输出更稳的模型
         max_tokens: 4096
         temperature: 0.3          # 低温度保证格式稳定
     ```
  2. `lightrag_engine.py` `_async_initialize` 中构建独立抽取函数并通过 `role_llm_configs` 注入：
     ```python
     role_llm_configs: dict[str, dict[str, Any]] = {
         "extract": {"max_async": 4},
         "keyword": {"max_async": 4},
         "query":   {"max_async": 4},
     }
     if extract_cfg.get("model"):
         extract_model = extract_cfg.get("model")
         extract_max_tokens = extract_cfg.get("max_tokens", 4096)
         extract_temperature = extract_cfg.get("temperature", 0.3)

         async def _extract_llm_func(prompt, system_prompt=None, **kwargs):
             messages = []
             if system_prompt:
                 messages.append({"role": "system", "content": system_prompt})
             messages.append({"role": "user", "content": prompt})
             response = await llm_client.chat.completions.create(
                 model=extract_model,
                 messages=messages,
                 max_tokens=extract_max_tokens,
                 temperature=extract_temperature,
             )
             return response.choices[0].message.content or ""

         role_llm_configs["extract"]["func"] = _extract_llm_func
     ```
  3. **踩坑**：LightRAG `RoleLLMConfig` 字段是 `func`（不是 `model_func`），用错会抛 `TypeError: RoleLLMConfig.__init__() got an unexpected keyword argument 'model_func'`
- **成效**：
  - 图谱规模从 132 → 2466 entities、53 → 2498 relations（抽取质量大幅提升）
  - 抽取阶段格式警告明显减少（仍有但不影响最终结果）

---

## 14. `_submit` 默认 120s 超时不够，大文档索引中断

- **发现时间**：2026-07-16（`kb index --force` 时 4 篇文档中 2 篇报"操作超时（300s）"）
- **涉及文件**：`src/knowledge_base/knowledge_graph/lightrag_engine.py`、`config.yaml`
- **问题现象**：CLI 输出 `索引失败: 传习录...md: 操作超时（300s）`，但部分 chunk 实际已成功存储（`text_chunks.json` 有数据），导致 doc_status 永远卡在 "processing"
- **产生原因**：
  - `_submit(coro, timeout=120)` 默认 120s 超时
  - 39 个 chunk × ~3s/次 LLM 抽取 ≈ 117s，正好踩在边界
  - 抽取阶段用 deepseek-chat 后更慢（~5s/次），39 chunk 需 ~200s+
- **解决方案**：
  1. `config.yaml` 新增 `index_timeout`：
     ```yaml
     knowledge_graph:
       index_timeout: 600   # 单文档索引超时（秒）
     ```
  2. `lightrag_engine.py` `index_document()` 使用该配置：
     ```python
     def index_document(self, doc_id, content, metadata=None):
         timeout = self.config.get("knowledge_graph", {}).get("index_timeout", 300)
         return self._submit(self.aindex_document(doc_id, content, metadata), timeout=timeout)
     ```
- **成效**：4 篇文档（含 67 chunk 的传习录）全部一次跑完，doc_status 全部 `[processed]`

---

## 15. `****` 星号污染：源文档 / LLM 输出 / UI 三层叠加

- **发现时间**：2026-07-16（用户反馈"为什么还是有这么多"*"号"）
- **涉及文件**：
  - `src/knowledge_base/utils.py`（新增 `clean_markdown_artifacts`）
  - `src/knowledge_base/importers/markdown_writer.py`（导入时调用）
  - `src/knowledge_base/knowledge_graph/lightrag_engine.py`（`aquery()` 输出后处理）
  - `src/knowledge_base/ui/app.py`（禁用 `_highlight_entities`）
- **问题现象**：用户回答中频繁出现 `文**理**`、`人****文`、`理********`、`空**理**、性理****` 等污染：
  - **ASCII `*`** 单独或连续（`**` / `****`）
  - **全角 `＊`**（PDF 抽取常见）
  - **算子 `∗`** / **实心 `✱`**（个别抽取器）
  - **抽取断裂**：`有****无`、`性****理`（抽取过程中字被切碎）
  - **UI 二次叠加**：单字实体如 `理`、`文` 被 UI 包裹成 `文**理**`、`人****文`
- **产生原因**：
  1. **源文档**：PDF/EPUB → Markdown 转换器输出带 `**xxx**` 强调
  2. **LLM 输出**：LLM 自己加 `**关键词**` 强调
  3. **UI 高亮**：`app.py:_highlight_entities` 用 `**name**` 包裹实体名
  4. 简单 `re.sub(r"\*+", "")` 无法处理全角、算子变体，也无法应对 UI 二次叠加
- **解决方案**（三层防护）：

  **第一层 - 源端**：`src/knowledge_base/utils.py` 新增 `clean_markdown_artifacts()`：
  ```python
  def clean_markdown_artifacts(text: str) -> str:
      if not text:
          return text
      # 各种星号变体（PDF/EPUB 抽取常见全角 / 算子）
      star_variants = r"[*＊∗✱]"

      # 1) 任意连续星号序列（覆盖 **, ****, ＊, ∗, ∗＊∗ 等所有组合）
      text = re.sub(rf"{star_variants}+", "", text)

      # 2) 兜底：CJK 与星号 / CJK 与星号 / 拉丁词与星号 的边界
      cjk = r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]"
      text = re.sub(rf"(?<={cjk}){star_variants}+(?={cjk})", "", text)
      text = re.sub(rf"(?<={cjk}){star_variants}+(?=\w)", "", text)
      text = re.sub(rf"(?<=\w){star_variants}+(?={cjk})", "", text)

      # 3) 反引号代码标记
      text = re.sub(r"`+", "", text)

      # 4) 连续下划线
      text = re.sub(r"_{2,}", "", text)

      # 5) 行尾 / 段尾孤立星号
      text = re.sub(rf"{star_variants}+\s*$", "", text, flags=re.MULTILINE)

      return text.strip()
  ```

  `src/knowledge_base/importers/markdown_writer.py` 写盘前调用：
  ```python
  from knowledge_base.utils import clean_markdown_artifacts
  content = frontmatter + "\n\n" + clean_markdown_artifacts(result.markdown or "")
  ```

  **第二层 - LLM 输出端**：`lightrag_engine.py` `aquery()` LLM 返回后兜底：
  ```python
  result_str = await asyncio.wait_for(
      self._llm_func(prompt=prompt_str, system_prompt=None),
      timeout=llm_remaining,
  )
  from knowledge_base.utils import clean_markdown_artifacts
  result_str = clean_markdown_artifacts(result_str)
  ```

  **第三层 - UI**：`src/knowledge_base/ui/app.py` 禁用高亮（避免二次叠加）：
  ```python
  def _highlight_entities(text: str, entities: list[dict]) -> str:
      """高亮实体名称（已禁用：会导致 UI 中出现大量 **** 污染）。

      原实现用 **xxx** 包实体名，但：
      1. LLM 输出里偶尔会有不规则的 * 残留，叠加后变成 文**理** / 人****文
      2. 单字实体（理 / 文 / 名）高亮后视觉上很碎

      现直接返回原文。如需重新启用高亮，改用 HTML/emoji 等非 markdown 标记。
      """
      return text
  ```

- **系统 prompt 方案无效**：尝试在 `aquery()` 调用 LLM 时加 `system_prompt="...请去除 Markdown 格式符号..."`，LLM 忽略该指令（指令遵循优先级不够）。故改为后处理
- **踩坑**：kb serve / kb ui 进程不会自动重载 Python 模块，必须 `kill <PID>` + 重启才能加载新代码
- **成效**：所有路径（kb query / kb ui / kb serve）回答完全干净，零 `*` 符号

---

## 16. PDF 导入静默失败，CLI 只报 "0 成功, 1 失败" 无具体原因

- **发现时间**：2026-07-16（重建索引时 4 篇中 PDF 篇失败）
- **涉及文件**：`src/knowledge_base/importers/batch.py`
- **问题现象**：`kb import test_data/复杂背景下基于深度学习的手势识别.pdf` 输出：
  ```
  INFO - 转换完成: 0 成功, 1 失败, 输出目录: kb_data
  ```
  无任何 error trace 或 reason
- **产生原因**（待完整排查，已知线索）：
  - BatchImporter `_process_one()` 中异常被吞掉，仅记录到 `results` 列表
  - `cli.py:_cmd_import` 只统计 `success_count` 和 `failed_count`，未打印失败原因
  - 可能原因：MinerU API Key 未配置 / PyMuPDF 依赖缺失 / 复杂排版 PDF 解析失败
- **当前状态**：PDF 文档暂未索引，4 篇中只成功 3 篇（哲学原论 / 传习录 / 刻意练习）
- **建议排查路径**：
  1. 临时在 `importers/batch.py` `_process_one` 异常分支加 `print(f"[DEBUG] Import failed: {e}", file=sys.stderr)`
  2. 检查 `config.yaml` 中 `mineru.api_key` 是否配置
  3. 测试 PDF 是否能被 `PyMuPDF` 直接打开（`pdf = fitz.open(path); print(pdf.page_count)`）
  4. 如 MinerU 未配置，PDF 走 `pymupdf` 引擎：`kb import --engine pymupdf "test_data/...pdf"`
- **待办**：补充完整失败原因后回填此 bug 记录

---

## 17. UI "已索引文档"列表显示 `unknown_source` 而不是真实标题

- **发现时间**：2026-07-16（用户反馈 UI 搜索结果下方"已索引文档"显示问题）
- **涉及文件**：`src/knowledge_base/ui/app.py`
- **问题现象**：UI 知识检索页查询后，展开"已索引文档 (3)"显示：
  ```
  unknown_source
  unknown_source
  unknown_source
  ```
  而不是真实的《传习录》《刻意练习》等标题
- **产生原因**：
  - `kv_store_full_docs.json` 中 `file_path` 字段值是 `"unknown_source"`（CLI 导入时未填，详见 bug #16）
  - `app.py:387-389` 原代码：
    ```python
    title = doc_id
    if doc_path:                              # 只判断非空，不判断文件是否存在
        title = Path(doc_path).stem
    ```
  - `Path("unknown_source").stem` 返回 `"unknown_source"`，覆盖了原本正确的 `doc_id`（即 `kv_store_full_docs.json` 的 key，本身就是文档标题）
- **解决方案**：`src/knowledge_base/ui/app.py` 知识检索页来源文档列表处加 `exists()` 判断：
  ```python
  # 来源文档
  with st.expander(f"已索引文档 ({len(docs)})"):
      for doc in docs:
          doc_id = doc.get("id", "")
          doc_path = doc.get("path", "")
          # 优先用 doc_id（即 kv_store_full_docs.json 的 key，本身就是标题）
          # 只有当 doc_path 是真实存在的文件时，才用文件名 stem 覆盖
          title = doc_id
          if doc_path and Path(doc_path).exists():
              title = Path(doc_path).stem
          st.write(f"- {title}")
  ```
  （`_render_document_list` 第 426-450 行已有 `Path(doc_path).exists()` 判断，无需修改）
- **成效**：UI 来源文档列表正确显示真实标题：`《中国哲学原论》前六章`、`传习录（全本全注全译）`、`刻意练习：如何从新手到大师`
- **根因关联**：底层数据问题（`file_path="unknown_source"`）见 bug #16，UI 端防御性兜底见本条
