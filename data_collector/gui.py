"""data_collector — 图形界面数据采集工具.

运行方式:
    python gui.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import messagebox, simpledialog, ttk

# Force stdout/stderr to match Windows console encoding (GBK for Chinese locale)
# to avoid garbled Chinese text
_console_encoding = "gbk"
if sys.stdout and sys.stdout.encoding != _console_encoding:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding=_console_encoding, errors="replace", line_buffering=True)
if sys.stderr and sys.stderr.encoding != _console_encoding:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding=_console_encoding, errors="replace", line_buffering=True)

# Ensure parent directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cleaner import Cleaner
from compliance import Compliance, ComplianceError
from config import load_config
from fetcher import Fetcher, FetchError
from data_parser import Parser
from saver import Saver


# ── GUI Logging Handler ─────────────────────────────────────────────

_log_queue: queue.Queue = queue.Queue()


class GuiLogHandler(logging.Handler):
    """Redirects logging records into a queue consumed by the GUI."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        _log_queue.put(msg)


# ── Main Application ────────────────────────────────────────────────

class CollectorApp:
    """Tkinter GUI for one-shot data collection."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Data Collector — 数据采集工具")
        self.root.geometry("820x680")
        self.root.minsize(700, 550)

        self._is_running = False
        self._last_output_path = ""
        self._dedup_fields = ["url"]
        self._zjlx_running = False  # 资金流向抓取状态

        self._build_ui()
        self._setup_logging()
        self._poll_logs()

    # ── UI Construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 创建主框架
        main_container = ttk.Frame(self.root, padding=12)
        main_container.pack(fill=tk.BOTH, expand=True)

        # 创建标签页控件
        self.notebook = ttk.Notebook(main_container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 标签页1：通用采集
        general_frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(general_frame, text="通用采集")
        self._build_general_tab(general_frame)

        # 标签页2：资金流向
        zjlx_frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(zjlx_frame, text="资金流向")
        self._build_zjlx_tab(zjlx_frame)

    def _build_general_tab(self, main: ttk.Frame) -> None:
        """构建通用采集标签页."""

        # ── Row 0: URL ──
        ttk.Label(main, text="目标 URL:").grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(main, textvariable=self.url_var, width=60)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky=tk.EW, pady=(0, 8), padx=(5, 0))
        main.columnconfigure(1, weight=1)

        # ── Row 1: Parse method ──
        ttk.Label(main, text="解析方式:").grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.parse_var = tk.StringVar(value="html")
        parse_frame = ttk.Frame(main)
        parse_frame.grid(row=1, column=1, sticky=tk.W, pady=(0, 8), padx=(5, 0))
        ttk.Radiobutton(parse_frame, text="自动分析", variable=self.parse_var, value="auto_analyze").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(parse_frame, text="HTML", variable=self.parse_var, value="html").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(parse_frame, text="JSON", variable=self.parse_var, value="json").pack(side=tk.LEFT)

        # ── Row 1b: Dynamic rendering checkbox ──
        self.dynamic_var = tk.BooleanVar(value=True)
        dyn_frame = ttk.Frame(main)
        dyn_frame.grid(row=1, column=2, sticky=tk.W, pady=(0, 8))
        self.dyn_check = ttk.Checkbutton(dyn_frame, text="动态渲染 (JS)", variable=self.dynamic_var)
        self.dyn_check.pack(side=tk.LEFT)

        # ── Row 2: Export format ──
        ttk.Label(main, text="导出格式:").grid(row=2, column=0, sticky=tk.W, pady=(0, 8))
        self.format_var = tk.StringVar(value="xlsx")
        fmt_frame = ttk.Frame(main)
        fmt_frame.grid(row=2, column=1, sticky=tk.W, pady=(0, 8), padx=(5, 0))
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(fmt_frame, text="Word (.docx)", variable=self.format_var, value="word").pack(side=tk.LEFT)

        # ── Row 3: Dedup fields ──
        ttk.Label(main, text="去重字段:").grid(row=3, column=0, sticky=tk.NW, pady=(0, 8))
        dedup_frame = ttk.Frame(main)
        dedup_frame.grid(row=3, column=1, sticky=tk.W, pady=(0, 8), padx=(5, 0))
        self.dedup_label = ttk.Label(dedup_frame, text="url")
        self.dedup_label.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(dedup_frame, text="添加", command=self._add_dedup_field).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(dedup_frame, text="清除", command=self._clear_dedup_fields).pack(side=tk.LEFT)

        # ── Row 4: HTML Rules ──
        self.html_label = ttk.Label(main, text="HTML规则:")
        self.html_label.grid(row=4, column=0, sticky=tk.NW, pady=(0, 4))
        html_frame = ttk.Frame(main)
        html_frame.grid(row=4, column=1, columnspan=2, sticky=tk.EW, pady=(0, 4), padx=(5, 0))
        main.columnconfigure(2, weight=1)

        # Selector type toggle
        self.selector_var = tk.StringVar(value="xpath")
        ttk.Label(html_frame, text="选择器:").grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(html_frame, text="CSS", variable=self.selector_var, value="css").grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(html_frame, text="XPath", variable=self.selector_var, value="xpath").grid(row=0, column=2, sticky=tk.W)

        # Row 1: Container/XPath 输入
        self.html_sel1_label = ttk.Label(html_frame, text="XPath:")
        self.html_sel1_label.grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self.html_sel1_var = tk.StringVar(value="//table//tr")
        self.html_sel1_entry = ttk.Entry(html_frame, textvariable=self.html_sel1_var, width=50)
        self.html_sel1_entry.grid(row=1, column=1, columnspan=2, padx=(5, 10), sticky=tk.EW, pady=(4, 0))
        html_frame.columnconfigure(1, weight=1)

        # Row 2: 字段定义 - 多个XPath用分号分隔
        self.html_sel2_label = ttk.Label(html_frame, text="字段XPath:")
        self.html_sel2_label.grid(row=2, column=0, sticky=tk.NW, pady=(4, 0))
        self.html_sel2_var = tk.StringVar(value="代码:.//td[1]; 名称:.//td[2]; 价格:.//td[3]")
        self.html_sel2_entry = ttk.Entry(html_frame, textvariable=self.html_sel2_var, width=50)
        self.html_sel2_entry.grid(row=2, column=1, columnspan=2, padx=(5, 0), pady=(4, 0), sticky=tk.EW)

        # Row 3: 提示
        ttk.Label(html_frame, text="提示: 字段格式为 字段名:XPath表达式，多个用分号分隔", foreground="gray").grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(2, 0))

        self._html_widgets = [self.html_label, html_frame]

        # ── Row 5: JSON Rules ──
        self.json_label = ttk.Label(main, text="JSON规则:")
        self.json_label.grid(row=5, column=0, sticky=tk.NW, pady=(0, 4))
        json_frame = ttk.Frame(main)
        json_frame.grid(row=5, column=1, columnspan=2, sticky=tk.EW, pady=(0, 4), padx=(5, 0))

        ttk.Label(json_frame, text="映射:").grid(row=0, column=0, sticky=tk.NW)
        self.json_rules_var = tk.StringVar(value="title:title; content:content; id:id")
        json_entry = ttk.Entry(json_frame, textvariable=self.json_rules_var, width=50)
        json_entry.grid(row=0, column=1, padx=(5, 0), sticky=tk.EW)
        json_frame.columnconfigure(1, weight=1)
        self._json_widgets = [self.json_label, json_frame]

        # ── Row 6: Log area ──
        ttk.Label(main, text="运行日志:").grid(row=6, column=0, sticky=tk.NW, pady=(10, 4))
        log_frame = ttk.Frame(main)
        log_frame.grid(row=6, column=1, columnspan=2, rowspan=2, sticky=tk.NSEW, pady=(10, 4), padx=(5, 0))
        main.rowconfigure(6, weight=1)

        self.log_text = tk.Text(log_frame, height=14, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Row 8: Buttons ──
        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=8, column=0, columnspan=3, pady=(8, 4), sticky=tk.EW)

        self.start_btn = ttk.Button(btn_frame, text="开始采集", command=self._start_collection)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_collection, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side=tk.LEFT, padx=(0, 8))
        self.open_btn = ttk.Button(btn_frame, text="打开输出目录", command=self._open_output_dir, state=tk.DISABLED)
        self.open_btn.pack(side=tk.LEFT)

        # ── Row 9: Status bar ──
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(main, textvariable=self.status_var, foreground="gray").grid(
            row=9, column=0, columnspan=3, sticky=tk.W, pady=(6, 0),
        )

        # Initial visibility
        self._on_parse_mode_change()
        self.parse_var.trace_add("write", self._on_parse_mode_change)
        self.format_var.trace_add("write", self._on_parse_mode_change)
        self.selector_var.trace_add("write", self._on_parse_mode_change)

    # ── UI Helpers ──────────────────────────────────────────────────

    def _on_parse_mode_change(self, *_args: object) -> None:
        mode = self.parse_var.get()
        fmt = self.format_var.get()
        show_html = mode == "html" and fmt != "word"
        show_json = mode == "json" and fmt != "word"
        sel = self.selector_var.get()

        # Auto-analyze mode: auto-enable dynamic rendering
        if mode == "auto_analyze":
            self.dynamic_var.set(True)
            self.dyn_check.config(state=tk.DISABLED)
        else:
            self.dyn_check.config(state=tk.NORMAL)

        # Update HTML labels and defaults based on selector type
        if sel == "xpath":
            self.html_sel1_label.config(text="XPath:")
            self.html_sel2_label.config(text="字段XPath:")
        else:
            self.html_sel1_label.config(text="容器:")
            self.html_sel2_label.config(text="字段:")

        for w in self._html_widgets:
            if show_html:
                w.grid()
            else:
                w.grid_remove()
        for w in self._json_widgets:
            if show_json:
                w.grid()
            else:
                w.grid_remove()

    def _add_dedup_field(self) -> None:
        field = simpledialog.askstring("添加去重字段", "字段名称:")
        if field and field.strip():
            field = field.strip()
            if field not in self._dedup_fields:
                self._dedup_fields.append(field)
                self.dedup_label.config(text=", ".join(self._dedup_fields))

    def _clear_dedup_fields(self) -> None:
        self._dedup_fields = []
        self.dedup_label.config(text="(无)")

    def _append_log(self, msg: str) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _open_output_dir(self) -> None:
        if self._last_output_path:
            directory = os.path.dirname(self._last_output_path)
            if sys.platform == "win32":
                os.startfile(directory)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", directory])
            else:
                subprocess.Popen(["xdg-open", directory])

    # ── Logging Setup ───────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = GuiLogHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        )
        handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(handler)

    def _poll_logs(self) -> None:
        """Check the log queue and display any pending messages."""
        while not _log_queue.empty():
            try:
                msg = _log_queue.get_nowait()
                self._append_log(msg)
            except queue.Empty:
                break
        self.root.after(100, self._poll_logs)

    # ── Collection Logic ────────────────────────────────────────────

    def _start_collection(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入目标 URL")
            return

        self._is_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.open_btn.config(state=tk.DISABLED)
        self.status_var.set("正在采集...")

        parse_mode = self.parse_var.get()
        export_fmt = self.format_var.get()
        dynamic = self.dynamic_var.get()
        dedup = list(self._dedup_fields) if self._dedup_fields else None

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(url, parse_mode, export_fmt, dedup, dynamic),
            daemon=True,
        )
        thread.start()

    def _stop_collection(self) -> None:
        self._is_running = False
        self.status_var.set("已停止")

    def _run_pipeline(self, url: str, parse_mode: str, export_fmt: str, dedup: list[str] | None, dynamic: bool = False) -> None:
        """Executed in a background thread."""
        try:
            config = load_config()
            if dynamic:
                from fetcher import DynamicFetcher
                fetcher = DynamicFetcher(config)
            else:
                fetcher = Fetcher(config)
            parser = Parser(config.parser.default_selector)
            cleaner = Cleaner(
                persist_dedup=config.cleaner.persist_dedup,
                persist_path=config.cleaner.persist_path,
            )
            compliance = Compliance(
                request_delay_min=config.compliance.request_delay_min,
                request_delay_max=config.compliance.request_delay_max,
                forbidden_keywords=config.compliance.forbidden_keywords,
                allowed_domains=config.compliance.allowed_domains,
            )
            saver = Saver(
                export_path=config.saver.export_path,
                log_level=config.saver.log_level,
                log_path=config.saver.log_path,
            )
            saver.setup_logging()

            logging.getLogger(__name__).info("开始采集: %s%s", url, " [动态渲染]" if dynamic else "")

            # Domain check
            try:
                compliance.check_domain(url)
            except ComplianceError as exc:
                logging.getLogger(__name__).error("合规校验失败: %s", exc)
                self.root.after(0, self._on_complete, 0, 0, 1, "", str(exc))
                return

            # Rate limit
            compliance.rate_limit()

            # Fetch
            try:
                if dynamic:
                    response = fetcher.fetch(url)
                else:
                    response = fetcher.fetch(url)
            except FetchError as exc:
                logging.getLogger(__name__).error("请求失败: %s", exc)
                self.root.after(0, self._on_complete, 0, 0, 1, "", str(exc))
                return

            if not self._is_running:
                self.root.after(0, self._on_complete, 0, 0, 0, "", "用户取消", True)
                return

            # Word mode: save page content directly as .docx
            if export_fmt == "word":
                try:
                    output_path = saver.to_word(response.text, url, filename="webpage")
                    logging.getLogger(__name__).info("网页内容已保存至: %s", output_path)
                except Exception as exc:
                    logging.getLogger(__name__).error("Word 导出失败: %s", exc)
                    self.root.after(0, self._on_complete, 0, 0, 1, "", str(exc))
                    return

                cleaner.close()
                self.root.after(0, self._on_complete, 1, 1, 0, output_path)
                return

            # Structured data mode: parse → clean → filter → export
            try:
                if parse_mode == "auto_analyze":
                    records = parser.parse_auto(response)
                    logging.getLogger(__name__).info("自动分析完成，共 %d 条记录", len(records))
                    if records:
                        self._log_data_preview(records)
                else:
                    rules = self._build_rules(parse_mode)
                    records = parser.parse(response, rules)
                    logging.getLogger(__name__).info("解析完成，共 %d 条记录", len(records))
            except Exception as exc:
                logging.getLogger(__name__).error("解析失败: %s", exc)
                records = []

            if not self._is_running:
                self.root.after(0, self._on_complete, 0, 0, 0, "", "用户取消", True)
                return

            # Clean & dedup
            records = cleaner.process(records, dedup_fields=dedup)

            # Filter sensitive fields
            records = compliance.filter_fields(records)

            # Validate
            compliance.validate(records)

            # Export
            output_path = ""
            if records:
                try:
                    output_path = saver.save(records, fmt=export_fmt)
                    logging.getLogger(__name__).info("数据已保存至: %s", output_path)
                except Exception as exc:
                    logging.getLogger(__name__).error("导出失败: %s", exc)

            cleaner.close()

            total = len(records)
            if total == 0 and not output_path:
                msg = "自动分析未找到可提取的结构化数据" if parse_mode == "auto_analyze" else "未找到符合条件的数据"
                logging.getLogger(__name__).warning(msg)
                self.root.after(0, self._on_complete, 0, 0, 0, "", msg)
            else:
                self.root.after(0, self._on_complete, total, total, 0, output_path)

        except Exception as exc:
            logging.getLogger(__name__).exception("采集过程异常: %s", exc)
            self.root.after(0, self._on_complete, 0, 0, 1, "", str(exc))

    def _build_rules(self, parse_mode: str) -> dict:
        """Build parsing rules based on user input and selected mode."""
        if parse_mode == "json":
            raw = self.json_rules_var.get().strip()
            mapping: dict[str, str] = {}
            for item in raw.split(";"):
                item = item.strip()
                if ":" in item:
                    key, path = item.split(":", 1)
                    mapping[key.strip()] = path.strip()
            return mapping

        # HTML mode
        sel_type = self.selector_var.get()
        sel1 = self.html_sel1_var.get().strip()
        sel2 = self.html_sel2_var.get().strip()

        # Parse fields: 格式为 "字段名:选择器" 或 "字段名:选择器:属性"，用分号分隔
        fields: dict[str, dict] = {}
        raw_fields = sel2
        for item in raw_fields.split(";"):
            item = item.strip()
            if not item or ":" not in item:
                continue
            # 分割字段名和选择器
            colon1 = item.index(":")
            name = item[:colon1].strip()
            rest = item[colon1 + 1:].strip()
            if not rest:
                continue
            # 检查是否有第三个冒号（属性）
            last_colon = rest.rfind(":")
            if last_colon > 0 and rest[last_colon + 1:].strip().isidentifier():
                selector = rest[:last_colon].strip()
                attr = rest[last_colon + 1:].strip()
            else:
                selector = rest
                attr = "text"
            fields[name] = {
                "selector": selector,
                "type": sel_type,
                "attribute": attr,
            }

        container = sel1 or ("//table//tr" if sel_type == "xpath" else "body")

        return {
            "container": {"selector": container, "type": sel_type},
            "fields": fields,
        }

    @staticmethod
    def _log_data_preview(records: list[dict[str, str]]) -> None:
        """Log field names and the first 3 records as a preview."""
        log = logging.getLogger(__name__)
        fields = list(records[0].keys())
        log.info("字段: %s", ", ".join(fields))
        for rec in records[:3]:
            preview = " | ".join(str(rec.get(f, ""))[:30] for f in fields)
            log.info("  示例: %s", preview)
        if len(records) > 3:
            log.info("  ... 以及 %d 条更多记录", len(records) - 3)

    def _on_complete(
        self,
        total: int,
        success: int,
        failed: int,
        output_path: str,
        error: str = "",
        cancelled: bool = False,
    ) -> None:
        """Called on the main thread when collection finishes."""
        self._is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

        if cancelled:
            self.status_var.set("已取消")
        elif output_path:
            self._last_output_path = output_path
            self.open_btn.config(state=tk.NORMAL)
            self.status_var.set(
                f"采集完成 | 总数:{total} | 成功:{success} | 失败:{failed} | 保存至: {output_path}",
            )
            messagebox.showinfo(
                "采集完成",
                f"成功采集 {success} 条记录\n\n"
                f"文件保存在:\n{output_path}\n\n"
                f"点击「打开输出目录」查看文件",
            )
        elif error:
            self.status_var.set(f"采集失败: {error}")
            messagebox.showerror("采集失败", error)

    # ── 资金流向抓取标签页 ────────────────────────────────────────

    def _build_zjlx_tab(self, parent: ttk.Frame) -> None:
        """构建资金流向抓取标签页."""
        # Row 0: 说明
        info_frame = ttk.LabelFrame(parent, text="功能说明", padding=8)
        info_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))
        ttk.Label(
            info_frame,
            text="抓取东方财富网资金流向数据，支持自动翻页、多市场选择，数据包含股票代码、名称、涨跌幅、主力资金流向等字段。",
            foreground="gray",
        ).pack(anchor=tk.W)

        # Row 1: 市场选择
        ttk.Label(parent, text="市场类型:").grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.zjlx_market_var = tk.StringVar(value="a股")
        market_frame = ttk.Frame(parent)
        market_frame.grid(row=1, column=1, columnspan=2, sticky=tk.W, pady=(0, 8), padx=(5, 0))

        markets = [("A股全市场", "a股"), ("沪市", "沪市"), ("深市", "深市"), ("创业板", "创业板"), ("科创板", "科创板")]
        for i, (text, value) in enumerate(markets):
            ttk.Radiobutton(market_frame, text=text, variable=self.zjlx_market_var, value=value).pack(
                side=tk.LEFT, padx=(0 if i == 0 else 10, 0)
            )

        # Row 2: 抓取模式
        ttk.Label(parent, text="抓取模式:").grid(row=2, column=0, sticky=tk.W, pady=(0, 8))
        self.zjlx_mode_var = tk.StringVar(value="api")
        mode_frame = ttk.Frame(parent)
        mode_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=(0, 8), padx=(5, 0))
        ttk.Radiobutton(mode_frame, text="API模式 (推荐，速度快)", variable=self.zjlx_mode_var, value="api").pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Radiobutton(mode_frame, text="浏览器模式 (兼容性好)", variable=self.zjlx_mode_var, value="browser").pack(
            side=tk.LEFT
        )

        # Row 3: 参数设置
        params_frame = ttk.LabelFrame(parent, text="参数设置", padding=8)
        params_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))

        # 页数
        ttk.Label(params_frame, text="抓取页数:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.zjlx_pages_var = tk.StringVar(value="10")
        pages_spin = ttk.Spinbox(params_frame, from_=1, to=100, textvariable=self.zjlx_pages_var, width=10)
        pages_spin.grid(row=0, column=1, sticky=tk.W, padx=(5, 20), pady=(0, 5))
        ttk.Label(params_frame, text="(每页约100条数据)", foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=(0, 5))

        # 每页数量（仅API模式）
        ttk.Label(params_frame, text="每页数量:").grid(row=1, column=0, sticky=tk.W, pady=(0, 5))
        self.zjlx_pagesize_var = tk.StringVar(value="100")
        pagesize_spin = ttk.Spinbox(params_frame, from_=20, to=500, increment=20, textvariable=self.zjlx_pagesize_var, width=10)
        pagesize_spin.grid(row=1, column=1, sticky=tk.W, padx=(5, 20), pady=(0, 5))
        ttk.Label(params_frame, text="(最大500，仅API模式)", foreground="gray").grid(row=1, column=2, sticky=tk.W, pady=(0, 5))

        # 渲染等待时间（仅浏览器模式）
        ttk.Label(params_frame, text="渲染等待:").grid(row=2, column=0, sticky=tk.W)
        self.zjlx_wait_var = tk.StringVar(value="3.0")
        wait_spin = ttk.Spinbox(params_frame, from_=1.0, to=10.0, increment=0.5, textvariable=self.zjlx_wait_var, width=10)
        wait_spin.grid(row=2, column=1, sticky=tk.W, padx=(5, 20))
        ttk.Label(params_frame, text="秒 (仅浏览器模式)", foreground="gray").grid(row=2, column=2, sticky=tk.W)

        # Row 4: 导出格式
        ttk.Label(parent, text="导出格式:").grid(row=4, column=0, sticky=tk.W, pady=(0, 8))
        self.zjlx_format_var = tk.StringVar(value="xlsx")
        format_frame = ttk.Frame(parent)
        format_frame.grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=(0, 8), padx=(5, 0))
        ttk.Radiobutton(format_frame, text="Excel (.xlsx)", variable=self.zjlx_format_var, value="xlsx").pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Radiobutton(format_frame, text="CSV", variable=self.zjlx_format_var, value="csv").pack(side=tk.LEFT)

        # Row 5: 进度显示
        progress_frame = ttk.LabelFrame(parent, text="抓取进度", padding=8)
        progress_frame.grid(row=5, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))

        self.zjlx_progress_var = tk.StringVar(value="等待开始...")
        ttk.Label(progress_frame, textvariable=self.zjlx_progress_var).pack(anchor=tk.W)

        self.zjlx_progress_bar = ttk.Progressbar(progress_frame, mode="determinate", length=400)
        self.zjlx_progress_bar.pack(fill=tk.X, pady=(5, 0))

        # Row 6: 日志区域
        ttk.Label(parent, text="运行日志:").grid(row=6, column=0, sticky=tk.NW, pady=(0, 4))
        log_frame = ttk.Frame(parent)
        log_frame.grid(row=6, column=1, columnspan=2, sticky=tk.NSEW, pady=(0, 4), padx=(5, 0))
        parent.rowconfigure(6, weight=1)
        parent.columnconfigure(1, weight=1)

        self.zjlx_log_text = tk.Text(log_frame, height=12, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.zjlx_log_text.yview)
        self.zjlx_log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.zjlx_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Row 7: 按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=7, column=0, columnspan=3, pady=(8, 4), sticky=tk.EW)

        self.zjlx_start_btn = ttk.Button(btn_frame, text="开始抓取", command=self._start_zjlx_collection)
        self.zjlx_start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.zjlx_stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_zjlx_collection, state=tk.DISABLED)
        self.zjlx_stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btn_frame, text="测试连接", command=self._test_zjlx_api).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="清空日志", command=self._clear_zjlx_log).pack(side=tk.LEFT, padx=(0, 8))

        self.zjlx_open_btn = ttk.Button(btn_frame, text="打开输出目录", command=self._open_output_dir, state=tk.DISABLED)
        self.zjlx_open_btn.pack(side=tk.LEFT)

        # Row 8: 状态栏
        self.zjlx_status_var = tk.StringVar(value="就绪")
        ttk.Label(parent, textvariable=self.zjlx_status_var, foreground="gray").grid(
            row=8, column=0, columnspan=3, sticky=tk.W, pady=(6, 0)
        )

    def _clear_zjlx_log(self) -> None:
        """清空资金流向日志."""
        self.zjlx_log_text.config(state=tk.NORMAL)
        self.zjlx_log_text.delete("1.0", tk.END)
        self.zjlx_log_text.config(state=tk.DISABLED)

    def _test_zjlx_api(self) -> None:
        """测试API连接."""
        self._append_zjlx_log("=" * 60)
        self._append_zjlx_log("开始测试API连接...")
        self._append_zjlx_log("=" * 60)

        market = self.zjlx_market_var.get()
        self._append_zjlx_log(f"市场: {market}")

        # 启动测试线程
        thread = threading.Thread(target=self._run_api_test, args=(market,), daemon=True)
        thread.start()

    def _run_api_test(self, market: str) -> None:
        """运行API测试（后台线程）."""
        try:
            # 市场筛选条件
            market_filters = {
                "a股": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "沪市": "m:1+t:2,m:1+t:23",
                "深市": "m:0+t:6,m:0+t:80",
                "创业板": "m:0+t:80",
                "科创板": "m:1+t:23",
            }

            base_url = "https://push2.eastmoney.com/api/qt/clist/get"
            fields = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152"

            params = {
                "fid": "f3",
                "po": "0",
                "pz": "5",  # 只取5条测试
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": market_filters.get(market, market_filters["a股"]),
                "fields": fields,
            }

            param_str = "&".join([f"{k}={v}" for k, v in params.items()])
            full_url = f"{base_url}?{param_str}"

            self.root.after(0, self._append_zjlx_log, f"URL: {full_url[:80]}...")
            self.root.after(0, self._append_zjlx_log, "发送请求...")

            req = urllib.request.Request(full_url)
            req.add_header(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            req.add_header("Referer", "https://data.eastmoney.com/zjlx/")

            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))

                self.root.after(0, self._append_zjlx_log, "✓ API响应成功")
                self.root.after(0, self._append_zjlx_log, f"响应码: {data.get('rc')}")

                if data.get("data"):
                    total = data["data"].get("total", 0)
                    diff = data["data"].get("diff", [])
                    self.root.after(0, self._append_zjlx_log, f"总记录数: {total}")
                    self.root.after(0, self._append_zjlx_log, f"当前页记录数: {len(diff)}")

                    if diff:
                        self.root.after(0, self._append_zjlx_log, "")
                        self.root.after(0, self._append_zjlx_log, "前3条记录示例:")
                        for i, record in enumerate(diff[:3], 1):
                            code = record.get("f12", "N/A")
                            name = record.get("f14", "N/A")
                            price = record.get("f2", "N/A")
                            change = record.get("f3", "N/A")
                            volume = record.get("f5", "N/A")
                            main_flow = record.get("f62", "N/A")
                            self.root.after(
                                0,
                                self._append_zjlx_log,
                                f"  {i}. {code} {name} - 价格:{price} 涨跌:{change}% 成交量:{volume} 主力:{main_flow}",
                            )

                        # 显示第一条记录的所有字段
                        if diff:
                            self.root.after(0, self._append_zjlx_log, "")
                            self.root.after(0, self._append_zjlx_log, "第一条记录的所有字段:")
                            first = diff[0]
                            for key in sorted(first.keys()):
                                value = first[key]
                                if value and value != "-":
                                    self.root.after(0, self._append_zjlx_log, f"  {key}: {value}")

                        self.root.after(0, self._append_zjlx_log, "")
                        self.root.after(0, self._append_zjlx_log, "✓ 测试成功！API工作正常")
                    else:
                        self.root.after(0, self._append_zjlx_log, "✗ 未返回数据")
                else:
                    self.root.after(0, self._append_zjlx_log, "✗ 响应格式异常")
                    self.root.after(0, self._append_zjlx_log, f"响应: {str(data)[:200]}")

        except Exception as e:
            self.root.after(0, self._append_zjlx_log, f"✗ 测试失败: {e}")
            import traceback

            error_msg = traceback.format_exc()
            self.root.after(0, self._append_zjlx_log, f"错误详情:\n{error_msg}")

        finally:
            self.root.after(0, self._append_zjlx_log, "=" * 60)

    def _append_zjlx_log(self, msg: str) -> None:
        """追加资金流向日志."""
        self.zjlx_log_text.config(state=tk.NORMAL)
        self.zjlx_log_text.insert(tk.END, msg + "\n")
        self.zjlx_log_text.see(tk.END)
        self.zjlx_log_text.config(state=tk.DISABLED)

    def _start_zjlx_collection(self) -> None:
        """开始资金流向数据抓取."""
        self._zjlx_running = True
        self.zjlx_start_btn.config(state=tk.DISABLED)
        self.zjlx_stop_btn.config(state=tk.NORMAL)
        self.zjlx_open_btn.config(state=tk.DISABLED)
        self.zjlx_status_var.set("正在抓取...")
        self.zjlx_progress_var.set("初始化...")
        self.zjlx_progress_bar["value"] = 0

        # 获取参数
        market = self.zjlx_market_var.get()
        mode = self.zjlx_mode_var.get()
        pages = int(self.zjlx_pages_var.get())
        page_size = int(self.zjlx_pagesize_var.get())
        export_format = self.zjlx_format_var.get()
        wait_time = float(self.zjlx_wait_var.get())

        # 启动后台线程
        thread = threading.Thread(
            target=self._run_zjlx_pipeline,
            args=(market, mode, pages, page_size, export_format, wait_time),
            daemon=True,
        )
        thread.start()

    def _stop_zjlx_collection(self) -> None:
        """停止资金流向数据抓取."""
        self._zjlx_running = False
        self.zjlx_status_var.set("已停止")

    def _run_zjlx_pipeline(
        self,
        market: str,
        mode: str,
        max_pages: int,
        page_size: int,
        export_format: str,
        wait_time: float,
    ) -> None:
        """资金流向抓取主逻辑（后台线程）."""
        try:
            config = load_config()
            cleaner = Cleaner(
                persist_dedup=config.cleaner.persist_dedup,
                persist_path=config.cleaner.persist_path,
            )
            compliance = Compliance(
                request_delay_min=config.compliance.request_delay_min,
                request_delay_max=config.compliance.request_delay_max,
                forbidden_keywords=config.compliance.forbidden_keywords,
                allowed_domains=config.compliance.allowed_domains,
            )
            saver = Saver(
                export_path=config.saver.export_path,
                log_level=config.saver.log_level,
                log_path=config.saver.log_path,
            )
            saver.setup_logging()

            log = logging.getLogger(__name__)
            log.info("开始抓取资金流向数据: 市场=%s, 模式=%s, 页数=%d", market, mode, max_pages)

            all_records = []

            if mode == "api":
                # API模式
                all_records = self._zjlx_api_mode(market, max_pages, page_size, compliance, log)
            else:
                # 浏览器模式
                all_records = self._zjlx_browser_mode(market, max_pages, wait_time, compliance, log)

            if not self._zjlx_running:
                self.root.after(0, self._on_zjlx_complete, 0, 0, 0, "", "用户取消", True)
                return

            # 去重
            if all_records:
                log.info("去重前: %d 条记录", len(all_records))
                all_records = cleaner.process(all_records, dedup_fields=["代码", "名称"])
                log.info("去重后: %d 条记录", len(all_records))

            # 过滤敏感字段
            all_records = compliance.filter_fields(all_records)

            # 导出
            output_path = ""
            if all_records:
                try:
                    # 验证数据有效性
                    if not all_records or not isinstance(all_records, list):
                        raise ValueError("数据为空或格式不正确")

                    # 检查第一条记录
                    if all_records and not all_records[0]:
                        log.warning("第一条记录为空，跳过导出")
                        all_records = [r for r in all_records if r]

                    if not all_records:
                        raise ValueError("过滤后数据为空")

                    # 检查数据内容
                    log.info("准备导出 %d 条记录", len(all_records))
                    if all_records:
                        first_record = all_records[0]
                        log.info("字段列表: %s", list(first_record.keys()))
                        log.info("第一条记录: %s", first_record)

                        # 检查是否所有字段都为空
                        all_empty = all(not v for v in first_record.values())
                        if all_empty:
                            log.warning("警告：第一条记录的所有字段都为空")

                    filename = f"eastmoney_{market}"
                    output_path = saver.save(all_records, fmt=export_format, filename=filename)
                    log.info("数据已导出至: %s", output_path)

                    # 验证文件是否创建成功
                    import os
                    if not os.path.exists(output_path):
                        raise FileNotFoundError(f"文件创建失败: {output_path}")

                    file_size = os.path.getsize(output_path)
                    log.info("文件大小: %.2f KB", file_size / 1024)

                    if file_size < 100:  # 文件太小，可能有问题
                        log.warning("文件大小异常，可能数据不完整")

                except Exception as exc:
                    log.error("导出失败: %s", exc)
                    import traceback
                    log.error("错误详情: %s", traceback.format_exc())
                    output_path = ""

            cleaner.close()

            total = len(all_records)
            if total == 0 and not output_path:
                msg = "未抓取到数据"
                log.warning(msg)
                self.root.after(0, self._on_zjlx_complete, 0, 0, 0, "", msg)
            else:
                self.root.after(0, self._on_zjlx_complete, total, total, 0, output_path)

        except Exception as exc:
            logging.getLogger(__name__).exception("抓取过程异常: %s", exc)
            self.root.after(0, self._on_zjlx_complete, 0, 0, 1, "", str(exc))

    def _zjlx_api_mode(
        self,
        market: str,
        max_pages: int,
        page_size: int,
        compliance: Compliance,
        log: logging.Logger,
    ) -> list[dict]:
        """API模式抓取."""
        all_records = []

        # 市场筛选条件
        market_filters = {
            "a股": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "沪市": "m:1+t:2,m:1+t:23",
            "深市": "m:0+t:6,m:0+t:80",
            "创业板": "m:0+t:80",
            "科创板": "m:1+t:23",
        }

        # 字段名称映射 - 使用完整的字段列表
        field_names = {
            "f12": "代码",
            "f14": "名称",
            "f2": "最新价",
            "f3": "涨跌幅",
            "f4": "涨跌额",
            "f5": "成交量",
            "f6": "成交额",
            "f7": "振幅",
            "f8": "换手率",
            "f9": "市盈率",
            "f10": "量比",
            "f15": "最高",
            "f16": "最低",
            "f17": "今开",
            "f18": "昨收",
            "f62": "主力净流入",
            "f66": "主力净比",
            "f69": "超大单净流入",
            "f72": "超大单净比",
            "f78": "大单净流入",
            "f84": "大单净比",
            "f90": "中单净流入",
            "f96": "中单净比",
            "f100": "小单净流入",
            "f106": "小单净比",
        }

        # 完整的API字段列表（东方财富需要特定格式）
        fields = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152"
        base_url = "https://push2.eastmoney.com/api/qt/clist/get"

        for page in range(1, max_pages + 1):
            if not self._zjlx_running:
                break

            # 更新进度
            progress = (page / max_pages) * 100
            self.root.after(0, self._update_zjlx_progress, f"正在抓取第 {page}/{max_pages} 页...", progress)

            # 构建URL
            params = {
                "fid": "f3",
                "po": "0",
                "pz": str(min(page_size, 500)),
                "pn": str(page),
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": market_filters.get(market, market_filters["a股"]),
                "fields": fields,
            }
            param_str = "&".join([f"{k}={v}" for k, v in params.items()])
            full_url = f"{base_url}?{param_str}"

            try:
                # 发送请求
                req = urllib.request.Request(full_url)
                req.add_header(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                req.add_header("Referer", "https://data.eastmoney.com/zjlx/")

                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read().decode("utf-8"))

                    # 调试：打印响应状态
                    log.debug("API响应: rc=%s, data存在=%s", data.get('rc'), data.get('data') is not None)

                    if data.get("data") and data["data"].get("diff"):
                        raw_records = data["data"]["diff"]
                        records = []

                        for raw in raw_records:
                            record = {}
                            for api_field, display_name in field_names.items():
                                value = raw.get(api_field, "")
                                if isinstance(value, str) and value == "-":
                                    value = ""
                                # 处理数值类型
                                if isinstance(value, (int, float)):
                                    value = str(value)
                                record[display_name] = value
                            records.append(record)

                        all_records.extend(records)
                        log.info("第 %d 页: 提取 %d 条记录", page, len(records))

                        # 更新日志 - 显示示例
                        self.root.after(0, self._append_zjlx_log, f"✓ 第 {page} 页: 提取 {len(records)} 条记录")
                        if records:
                            # 显示第一条记录作为示例
                            sample = records[0]
                            sample_str = f"  示例: {sample.get('代码', '')} {sample.get('名称', '')} - {sample.get('最新价', '')}"
                            self.root.after(0, self._append_zjlx_log, sample_str)
                    else:
                        log.info("第 %d 页: 无数据，停止翻页", page)
                        self.root.after(0, self._append_zjlx_log, f"✗ 第 {page} 页: 无数据，停止翻页")
                        break

            except Exception as exc:
                log.error("第 %d 页抓取失败: %s", page, exc)
                self.root.after(0, self._append_zjlx_log, f"✗ 第 {page} 页抓取失败: {exc}")
                # 打印详细错误信息
                import traceback
                log.debug("错误详情: %s", traceback.format_exc())
                break

            # 页间延迟
            if page < max_pages and self._zjlx_running:
                compliance.rate_limit()

        return all_records

    def _zjlx_browser_mode(
        self,
        market: str,
        max_pages: int,
        wait_time: float,
        compliance: Compliance,
        log: logging.Logger,
    ) -> list[dict]:
        """浏览器模式抓取."""
        try:
            from fetcher import DynamicFetcher
            from data_parser import Parser

            config = load_config()
            fetcher = DynamicFetcher(config)
            parser = Parser(config.parser.default_selector)

            all_records = []
            base_url = "https://data.eastmoney.com/zjlx/detail.html"

            for page in range(1, max_pages + 1):
                if not self._zjlx_running:
                    break

                # 更新进度
                progress = (page / max_pages) * 100
                self.root.after(0, self._update_zjlx_progress, f"正在抓取第 {page}/{max_pages} 页...", progress)

                # 构建URL
                url = f"{base_url}?pn={page}"

                try:
                    # 获取页面
                    compliance.rate_limit()
                    response = fetcher.fetch(url, wait_time=wait_time)

                    # 自动解析
                    records = parser.parse_auto(response)

                    if not records:
                        log.info("第 %d 页: 无数据，停止翻页", page)
                        self.root.after(0, self._append_zjlx_log, f"第 {page} 页: 无数据，停止翻页")
                        break

                    all_records.extend(records)
                    log.info("第 %d 页: 提取 %d 条记录", page, len(records))
                    self.root.after(0, self._append_zjlx_log, f"第 {page} 页: 提取 {len(records)} 条记录")

                except Exception as exc:
                    log.error("第 %d 页抓取失败: %s", page, exc)
                    self.root.after(0, self._append_zjlx_log, f"第 {page} 页抓取失败: {exc}")
                    break

            return all_records

        except ImportError:
            log.error("Playwright未安装，无法使用浏览器模式")
            self.root.after(0, self._append_zjlx_log, "错误: Playwright未安装，请使用API模式")
            return []

    def _update_zjlx_progress(self, status: str, progress: float) -> None:
        """更新进度显示."""
        self.zjlx_progress_var.set(status)
        self.zjlx_progress_bar["value"] = progress

    def _on_zjlx_complete(
        self,
        total: int,
        success: int,
        failed: int,
        output_path: str,
        error: str = "",
        cancelled: bool = False,
    ) -> None:
        """资金流向抓取完成回调."""
        self._zjlx_running = False
        self.zjlx_start_btn.config(state=tk.NORMAL)
        self.zjlx_stop_btn.config(state=tk.DISABLED)

        if cancelled:
            self.zjlx_status_var.set("已取消")
            self._update_zjlx_progress("已取消", 0)
        elif output_path:
            self._last_output_path = output_path
            self.zjlx_open_btn.config(state=tk.NORMAL)
            self.zjlx_status_var.set(f"抓取完成 | 总数:{total} | 保存至: {output_path}")
            self._update_zjlx_progress("抓取完成", 100)
            messagebox.showinfo(
                "抓取完成",
                f"成功抓取 {success} 条记录\n\n"
                f"文件保存在:\n{output_path}\n\n"
                f"点击「打开输出目录」查看文件",
            )
        elif error:
            self.zjlx_status_var.set(f"抓取失败: {error}")
            self._update_zjlx_progress("抓取失败", 0)
            messagebox.showerror("抓取失败", error)
        else:
            self.zjlx_status_var.set("未抓取到数据")
            self._update_zjlx_progress("未抓取到数据", 0)


# ── Entry Point ─────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()
    CollectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
