

# Python 模块化数据采集工具 — 正式开发需求文档

> 版本：V2.0  
> 日期：2026-05-01  
> 状态：待开发

---

## 一、项目概述

开发一套**企业级、高可用、合规化**的 Python 数据采集工具。采用模块化、面向对象设计，代码结构清晰易维护；具备完善的反爬绕过、异常容错能力；集成全自动数据清洗、去重与标准化导出；全程遵守网络安全与数据合规规范，仅支持公开数据采集。

---

## 二、技术栈规范

| 类别 | 技术选型 | 说明 |
|------|----------|------|
| 开发语言 | Python 3.10+（推荐 3.11） | 统一版本，避免兼容性问题 |
| 网络请求 | httpx | 主请求库，支持同步/异步；Fetcher 内部封装统一接口 |
| 降级请求 | requests | 仅在 httpx 不可用场景下作为备选，对外接口一致 |
| HTML解析 | BeautifulSoup4 + lxml | CSS 选择器为主，XPath 为可选增强 |
| JSON解析 | 内置 json + 自定义安全提取器 | 嵌套字段安全访问，键不存在不报错 |
| 动态渲染 | Playwright | 异步渲染动态页面（可选模块，按需加载） |
| 数据处理 | Pandas | 清洗、去重、导出 |
| 日志 | logging（标准库） | 分级日志，支持文件+控制台双输出 |
| 配置管理 | YAML + dataclass | 集中管理所有可配置参数 |

---

## 三、项目文件结构

```
data_collector/
├── config.yaml              # 统一配置文件
├── config.py                # 配置加载与数据类定义
├── fetcher.py               # 采集层：请求模块
├── parser.py                # 解析层：数据提取模块
├── cleaner.py               # 清洗层：去重与标准化模块
├── saver.py                 # 存储层：导出与日志模块
├── compliance.py            # 合规层：限流与安全控制
├── pipeline.py              # 流程编排：串联各模块
├── main.py                  # 入口示例
├── proxy_provider.py        # 代理池接口（抽象类 + 示例实现）
├── requirements.txt         # 依赖清单
└── tests/                   # 单元测试
    ├── test_fetcher.py
    ├── test_parser.py
    ├── test_cleaner.py
    └── test_saver.py
```

---

## 四、核心功能模块详细需求

### 模块 1：配置管理 (`config.py` + `config.yaml`)

**职责：** 集中管理所有可配置参数，避免硬编码。

**config.yaml 结构：**
```yaml
fetcher:
  timeout: 30              # 请求超时（秒）
  max_retries: 3           # 最大重试次数
  retry_backoff_base: 2    # 指数退避基数（秒）
  user_agent_rotation: true

compliance:
  request_delay_min: 1.0   # 最小请求间隔（秒）
  request_delay_max: 3.0   # 最大请求间隔（秒）
  forbidden_keywords:       # 禁止采集的字段关键词
    - phone
    - mobile
    - id_card
    - password
    - email
  allowed_domains: []       # 允许采集的域名白名单，空=不限制

cleaner:
  dedup_fields: ["url"]    # 默认去重字段
  persist_dedup: false     # 是否持久化去重记录
  persist_path: "dedup.db" # 去重持久化文件路径

saver:
  export_format: "xlsx"    # 默认导出格式: xlsx / csv
  export_path: "output"    # 导出目录
  log_level: "INFO"        # 日志级别
  log_path: "logs/app.log"

parser:
  default_selector: "css"  # 默认选择器类型: css / xpath
```

**config.py 要求：**
- 使用 `dataclass` 定义配置结构，类型安全
- 支持 YAML 文件加载，缺失字段使用默认值
- 支持环境变量覆盖（如 `FETCHER_TIMEOUT=60`）

---

### 模块 2：采集层 (`fetcher.py`)

**类名：** `Fetcher`

**核心方法：**

| 方法 | 说明 |
|------|------|
| `get(url, headers, cookies, proxy)` | 发送 GET 请求，返回 `httpx.Response` |
| `post(url, data, json, headers, proxy)` | 发送 POST 请求，支持 form/json body |
| `fetch(url, method, **kwargs)` | 统一入口，自动重试 + 退避 + 日志 |
| `fetch_api(url, **kwargs)` | 快捷方法，自动解析 JSON 返回 dict |
| `set_proxy_provider(provider)` | 注入代理提供器实例 |

**详细要求：**

1. **请求头管理**
   - 内置 User-Agent 池（不少于 20 条），每次请求随机选取
   - 支持调用方传入自定义 headers，与默认 headers 合并（自定义优先）

2. **代理支持**
   - 静态代理：通过 `proxy` 参数直接传入 `{"http": "...", "https": "..."}`
   - 动态代理：通过 `ProxyProvider` 抽象类注入，每次请求前调用 `get_proxy()` 获取最新代理
   - 代理失败时自动标记，切换下一个代理重试

3. **重试机制**
   - 超时、5xx、连接错误触发自动重试
   - 退避策略：`delay = retry_backoff_base ^ attempt + random(0, 1)` 秒
   - 重试次数由 `max_retries` 控制，全部失败抛出 `FetchError` 异常

4. **超时控制**
   - 统一超时时间，可通过配置修改，也可单次调用覆盖

5. **异常定义**
   - 自定义异常类：`FetchError`（请求失败）、`ProxyError`（代理失效）

6. **日志输出**
   - 每次请求输出：`[INFO] GET https://example.com -> 200 (1.2s)`
   - 失败输出：`[ERROR] GET https://example.com -> ConnectionError (retry 2/3)`

---

### 模块 3：解析层 (`parser.py`)

**类名：** `Parser`

**核心方法：**

| 方法 | 说明 |
|------|------|
| `parse_html(html, rules)` | 按规则解析 HTML，返回 `List[Dict]` |
| `parse_json(data, mapping)` | 安全提取 JSON 嵌套字段，返回 `List[Dict]` |
| `parse(response, rules)` | 自动判断响应类型（HTML/JSON），分发处理 |

**规则定义格式（`rules` 参数）：**

```python
# HTML 解析规则示例
html_rules = {
    "container": {"selector": "div.article-item", "type": "css"},   # 列表容器
    "fields": {
        "title": {"selector": "h2.title", "type": "css", "attribute": "text"},
        "link":  {"selector": "a", "type": "css", "attribute": "href"},
        "date":  {"selector": "span.date", "type": "css", "attribute": "text"},
    }
}

# JSON 解析映射示例
json_mapping = {
    "title": "data.articles[*].title",
    "author": "data.articles[*].author.name",
}
```

**详细要求：**

1. **HTML 解析**
   - 默认使用 CSS 选择器（BeautifulSoup），`type: "xpath"` 时使用 lxml XPath
   - `attribute` 支持：`"text"`（取文本）、`"href"`、`"src"`、任意属性名
   - 容器不存在时返回空列表，字段缺失时填充空字符串

2. **JSON 解析**
   - 支持路径表达式：`data.items[*].name`（`[*]` 表示数组遍历）
   - 路径中任意层级不存在时，填充 `None` 而非抛出异常
   - 自动展平为 `List[Dict]` 格式

3. **统一输出**
   - 所有解析方法统一返回 `List[Dict]`，每个 dict 代表一条记录
   - 空结果返回 `[]`，不返回 `None`

4. **异常处理**
   - 解析过程不允许抛出未捕获异常
   - 解析失败记录到日志：`[WARNING] 字段 'title' 解析失败，已填充空值`

---

### 模块 4：清洗与去重层 (`cleaner.py`)

**类名：** `Cleaner`

**核心方法：**

| 方法 | 说明 |
|------|------|
| `clean(data)` | 基础清洗：去HTML标签、空格、隐形字符 |
| `deduplicate(data, fields)` | 按指定字段去重 |
| `format_text(text)` | 文本标准化（全角转半角等） |
| `process(data, dedup_fields)` | 一键清洗+去重，返回干净数据 |

**详细要求：**

1. **基础清洗（`clean`）**
   - 去除所有 HTML/XML 标签（保留文本内容）
   - 去除首尾空格、制表符、换行符
   - 去除零宽字符（`\u200b`、`\u200c` 等隐形字符）
   - 去除不可见控制字符（保留正常换行）
   - 连续多个空格压缩为单个空格
   - 对 `List[Dict]` 中所有字符串值自动执行

2. **智能去重（`deduplicate`）**
   - **内存去重（默认）：** 使用 `set` 记录已见值，保留首次出现的数据
   - **持久化去重（可选）：** 使用 SQLite 存储已采集标识
     - `persist_dedup: true` 时启用
     - 支持跨运行去重（程序重启后不重复采集）
     - 提供 `reset_dedup()` 方法清空去重记录
   - 支持多字段联合去重：`fields=["url", "title"]` 时两者均相同才视为重复

3. **文本格式化（`format_text`）**
   - 全角字母/数字转半角
   - 统一 Unicode 规范化（NFC 形式）
   - 去除特殊不可见符号

4. **输出保证**
   - 保留原始数据类型（数字仍是数字，仅处理字符串）
   - 不修改原始数据，返回新数据副本

---

### 模块 5：存储导出层 (`saver.py`)

**类名：** `Saver`

**核心方法：**

| 方法 | 说明 |
|------|------|
| `to_excel(data, filename, sheet_name)` | 导出为 .xlsx |
| `to_csv(data, filename)` | 导出为 .csv |
| `save(data, format, **kwargs)` | 统一导出入口 |
| `setup_logging()` | 初始化日志系统 |
| `to_database(data, connection_string, table)` | 预留接口（本次可不实现） |

**详细要求：**

1. **Excel 导出**
   - 使用 Pandas `to_excel()`，引擎 `openpyxl`
   - 自动调整列宽（按内容最大宽度 + 2）
   - 表头加粗、冻结首行、自动筛选
   - 文件命名自动加时间戳：`data_20260501_143052.xlsx`
   - 文件路径不存在时自动创建目录

2. **CSV 导出**
   - UTF-8 with BOM 编码（兼容 Excel 直接打开不乱码）
   - 默认逗号分隔

3. **日志系统**
   - 同时输出到控制台和文件
   - 格式：`[2026-05-01 14:30:52] [INFO] [Fetcher] GET https://xxx -> 200`
   - 日志文件按天轮转，保留最近 7 天
   - 采集结束时输出汇总：`采集完成 | 总数:100 | 成功:98 | 失败:2`

4. **数据库写入（预留）**
   - `to_database()` 方法定义签名，文档注明"V2 实现"
   - 架构上支持后续无侵入扩展

---

### 模块 6：合规控制层 (`compliance.py`)

**类名：** `Compliance`

**核心方法：**

| 方法 | 说明 |
|------|------|
| `check_domain(url)` | 域名白名单校验 |
| `filter_fields(data)` | 过滤禁止采集的敏感字段 |
| `rate_limit()` | 请求限流控制 |
| `validate(response)` | 检查响应是否合规 |

**详细要求：**

1. **请求限流**
   - 每次请求前自动调用 `rate_limit()`
   - 随机休眠 `[request_delay_min, request_delay_max]` 秒
   - 对外提供 `set_delay_range(min, max)` 动态调整

2. **敏感字段过滤**
   - 遍历 `List[Dict]`，删除 key 名称匹配 `forbidden_keywords` 的字段
   - 匹配规则：key 名**包含**关键词即命中（不区分大小写）
   - 例：`"user_phone"` → 命中 `"phone"`，删除
   - 删除操作记录日志：`[INFO] 已过滤敏感字段: user_phone`

3. **域名白名单**
   - `allowed_domains` 非空时，仅允许采集白名单内域名
   - 不在白名单的请求抛出 `ComplianceError` 异常

4. **响应合规检查**
   - 检查响应中是否包含 `forbidden_keywords` 对应的值模式（如身份证号正则）
   - 命中时发出 `[WARNING]` 但**不自动删除**（避免误判），交由开发者决策

5. **异常定义**
   - `ComplianceError`：合规校验不通过

---

### 模块 7：流程编排 (`pipeline.py`)

**类名：** `Pipeline`

**职责：** 串联 Fetcher → Parser → Cleaner → Compliance → Saver，提供一键式采集接口。

**核心方法：**

```python
class Pipeline:
    def __init__(self, config_path="config.yaml"):
        """初始化所有模块"""
        
    def run(self, url, rules, dedup_fields=None, export_format="xlsx"):
        """一键采集：请求 → 解析 → 清洗 → 过滤 → 导出
        Returns: dict {'total': int, 'success': int, 'failed': int, 'output_path': str}
        """
    
    def run_multi(self, urls, rules):
        """批量采集多个 URL"""
```

---

### 模块 8：代理提供器接口 (`proxy_provider.py`)

```python
from abc import ABC, abstractmethod

class ProxyProvider(ABC):
    """代理池抽象接口"""
    
    @abstractmethod
    def get_proxy(self) -> dict:
        """返回格式: {"http": "http://ip:port", "https": "http://ip:port"}"""
        pass
    
    @abstractmethod
    def mark_bad(self, proxy: dict):
        """标记代理失效"""
        pass
```

**交付时附带一个 `SimpleProxyProvider` 示例实现**（基于静态列表轮询）。

---

## 五、开发规范

1. **面向对象 (OOP)**：所有模块为独立类，通过构造函数注入依赖
2. **高内聚低耦合**：模块之间通过明确接口交互，不直接访问对方内部属性
3. **类型注解**：所有公共方法必须包含完整的类型注解（Type Hints）
4. **异常处理**：
   - 自定义异常继承自 `CollectorError`（基类）
   - 全链路 try-except，模块不因单条数据异常而崩溃
   - 异常信息写入日志，包含上下文（URL、参数、堆栈）
5. **命名规范**：
   - 类名：大驼峰（`Fetcher`, `DataCleaner`）
   - 方法/函数：小写下划线（`fetch_page`, `clean_text`）
   - 常量：大写下划线（`MAX_RETRIES`）
   - 私有方法：单下划线前缀（`_build_headers`）
6. **文档注释**：
   - 每个类和公共方法使用 docstring（Google 风格）
   - 包含参数说明、返回值说明、可能抛出的异常

---

## 六、单元测试要求

- **框架：** pytest
- **覆盖率：** 核心模块 ≥ 70%
- **测试范围：**

| 模块 | 测试要点 |
|------|----------|
| Fetcher | Mock 请求，验证重试、超时、代理切换、请求头随机 |
| Parser | HTML 提取正确性、JSON 路径解析、异常字段处理 |
| Cleaner | HTML 标签清除、空格处理、去重逻辑、持久化去重 |
| Saver | Excel/CSV 导出、文件存在性、日志输出格式 |
| Compliance | 敏感字段过滤、域名白名单、限流触发 |

---

## 七、交付物清单

| 序号 | 交付物 | 说明 |
|------|--------|------|
| 1 | 完整源码 | 按上述文件结构组织 |
| 2 | `main.py` 示例 | 至少 3 个可直接运行的示例（静态页/API/批量采集） |
| 3 | `requirements.txt` | 精确版本的依赖清单 |
| 4 | 模块调用文档 | `README.md`，含架构图、快速开始、API 说明 |
| 5 | 单元测试代码 | `tests/` 目录下完整测试 |
| 6 | 配置文件模板 | `config.yaml` 含全部可配置项及注释 |

---

## 八、依赖清单参考 (`requirements.txt`)

```
httpx>=0.27.0
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=5.1.0
pandas>=2.2.0
openpyxl>=3.1.0
playwright>=1.40.0
pyyaml>=6.0
pytest>=8.0.0
pytest-mock>=3.12.0
```

