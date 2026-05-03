"""手动测试东方财富API - 查看原始返回数据.

运行方式：
    python 手动测试API.py
"""

import json
import urllib.request

def test_api():
    """测试API并显示原始数据."""

    # API URL
    base_url = "https://push2.eastmoney.com/api/qt/clist/get"

    # 完整参数
    params = {
        "fid": "f3",
        "po": "0",
        "pz": "3",  # 只取3条
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # A股
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
    }

    # 构建URL
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    full_url = f"{base_url}?{param_str}"

    print("="*80)
    print("东方财富API测试")
    print("="*80)
    print(f"URL: {full_url}")
    print()

    try:
        # 创建请求
        req = urllib.request.Request(full_url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        req.add_header("Referer", "https://data.eastmoney.com/zjlx/")

        print("发送请求...")
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

            print("✓ 请求成功!\n")

            # 显示响应结构
            print("响应结构:")
            print(f"  rc: {data.get('rc')}")
            print(f"  rt: {data.get('rt')}")
            print()

            # 检查数据
            if data.get("data"):
                print(f"✓ data存在")
                print(f"  total: {data['data'].get('total')}")

                if data["data"].get("diff"):
                    records = data["data"]["diff"]
                    print(f"  diff数组: {len(records)} 条记录\n")

                    # 显示每条记录
                    for i, record in enumerate(records, 1):
                        print(f"记录 {i}:")
                        print(f"  代码 (f12): {record.get('f12')}")
                        print(f"  名称 (f14): {record.get('f14')}")
                        print(f"  最新价 (f2): {record.get('f2')}")
                        print(f"  涨跌幅 (f3): {record.get('f3')}")
                        print(f"  成交量 (f5): {record.get('f5')}")
                        print(f"  主力净流入 (f62): {record.get('f62')}")
                        print()

                    # 保存完整响应到文件
                    with open("api_response.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print("完整响应已保存到: api_response.json")

                else:
                    print("  ✗ diff数组为空")
            else:
                print("✗ data字段不存在")
                print("\n完整响应:")
                print(json.dumps(data, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f"✗ 请求失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_api()
