"""
统一行情数据入口 —— 自建 Tushare 接口初始化。

后续所有取数脚本统一这样用，不要再各自写 token / http_url：

    from tushare_client import get_pro
    pro = get_pro()
    df = pro.index_basic(limit=5)

    # ⚠️ pro_bar 必须显式传 api=pro：
    import tushare as ts
    df = ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3)

接口地址: http://111.170.140.159:8020/
Token 来源（按优先级）: 环境变量 TUSHARE_TOKEN > 同目录 tushare_token.txt
若报 "Token 不对"，多半是漏了把 _DataApi__http_url 指向自建接口（本文件已处理）。
"""
import os
import tushare as ts

TUSHARE_HTTP_URL = os.environ.get("TUSHARE_HTTP_URL", "http://111.170.140.159:8020/")


def _load_token() -> str:
    tok = os.environ.get("TUSHARE_TOKEN")
    if tok:
        return tok.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "tushare_token.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    raise RuntimeError(
        "未找到 Tushare token：请设置环境变量 TUSHARE_TOKEN，"
        "或在本文件同目录创建 tushare_token.txt 写入 token。"
    )


_pro = None


def get_pro():
    """返回配置好的 tushare pro 对象（已指向自建接口，单例）。"""
    global _pro
    if _pro is None:
        _pro = ts.pro_api(_load_token())
        # 关键：指向自建接口，否则会报 Token 不对
        _pro._DataApi__http_url = TUSHARE_HTTP_URL
    return _pro


if __name__ == "__main__":
    pro = get_pro()
    print("== index_basic(limit=5) ==")
    print(pro.index_basic(limit=5))
    print("\n== pro_bar 000001.SZ limit=3 ==")
    print(ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3))
