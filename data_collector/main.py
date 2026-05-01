"""data_collector — 数据采集工具示例入口.

运行方式:
    python main.py             # 运行全部示例
    python main.py --example 1 # 仅运行示例 1
    python main.py --example 2 # 仅运行示例 2
    python main.py --example 3 # 仅运行示例 3
"""

from __future__ import annotations

import io
import sys

# Force stdout/stderr to GBK for Chinese Windows console
if sys.stdout and sys.stdout.encoding != "gbk":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="gbk", errors="replace", line_buffering=True)
if sys.stderr and sys.stderr.encoding != "gbk":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="gbk", errors="replace", line_buffering=True)

import argparse

from pipeline import Pipeline


# ── 示例 1：静态 HTML 页面采集 ───────────────────────────────────────

def example_static_page() -> None:
    """示例 1：采集静态 HTML 页面（以维基百科为例）。"""
    print("=" * 60)
    print("示例 1：静态 HTML 页面采集")
    print("=" * 60)

    pipeline = Pipeline()

    html_rules = {
        "container": {"selector": "div#mp-tfa", "type": "css"},
        "fields": {
            "title": {"selector": "p b", "type": "css", "attribute": "text"},
            "content": {"selector": "p", "type": "css", "attribute": "text"},
        },
    }

    result = pipeline.run(
        url="https://en.wikipedia.org/wiki/Main_Page",
        rules=html_rules,
        export_format="xlsx",
    )
    print(f"采集结果: {result}")
    pipeline.close()


# ── 示例 2：JSON API 采集 ──────────────────────────────────────────

def example_json_api() -> None:
    """示例 2：采集 JSON API 数据（以 JSONPlaceholder 为例）。"""
    print("=" * 60)
    print("示例 2：JSON API 数据采集")
    print("=" * 60)

    pipeline = Pipeline()

    json_mapping = {
        "id": "id",
        "title": "title",
        "body": "body",
        "userId": "userId",
    }

    result = pipeline.run(
        url="https://jsonplaceholder.typicode.com/posts",
        rules=json_mapping,
        dedup_fields=["id"],
        export_format="csv",
    )
    print(f"采集结果: {result}")
    pipeline.close()


# ── 示例 3：批量采集多个 URL ────────────────────────────────────────

def example_batch() -> None:
    """示例 3：批量采集多个 URL。"""
    print("=" * 60)
    print("示例 3：批量采集多个 URL")
    print("=" * 60)

    pipeline = Pipeline()

    html_rules = {
        "container": {"selector": "div.mw-body-content p", "type": "css"},
        "fields": {
            "text": {"selector": "p", "type": "css", "attribute": "text"},
        },
    }

    urls = [
        "https://en.wikipedia.org/wiki/Main_Page",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ]

    result = pipeline.run_multi(
        urls=urls,
        rules=html_rules,
        dedup_fields=["text"],
        export_format="xlsx",
    )
    print(f"采集结果: {result}")
    pipeline.close()


# ── 入口 ─────────────────────────────────────────────────────────────

EXAMPLES = {
    1: ("静态 HTML 页面采集", example_static_page),
    2: ("JSON API 数据采集", example_json_api),
    3: ("批量采集", example_batch),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="data_collector 示例程序")
    parser.add_argument(
        "--example",
        type=int,
        choices=[0, 1, 2, 3],
        default=0,
        help="运行指定示例 (0=全部)",
    )
    args = parser.parse_args()

    if args.example == 0:
        for num, (desc, func) in EXAMPLES.items():
            try:
                func()
            except Exception as exc:
                print(f"[错误] 示例 {num} 执行失败: {exc}")
            print()
    else:
        desc, func = EXAMPLES[args.example]
        try:
            func()
        except Exception as exc:
            print(f"[错误] 示例 {args.example} 执行失败: {exc}")


if __name__ == "__main__":
    main()
