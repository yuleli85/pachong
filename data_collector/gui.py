"""data_collector — 图形界面数据采集工具.

运行方式:
    python gui.py
"""

from __future__ import annotations

import io
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
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
        self.root.geometry("780x640")
        self.root.minsize(650, 500)

        self._is_running = False
        self._last_output_path = ""
        self._dedup_fields = ["url"]

        self._build_ui()
        self._setup_logging()
        self._poll_logs()

    # ── UI Construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

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


# ── Entry Point ─────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()
    CollectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
