from __future__ import annotations

import os
from typing import Any


DEFAULT_TUSHARE_TOKEN = os.getenv(
    "TUSHARE_TOKEN",
    "a37dd277b38b5a5d46f68d51eb1fdaa20e8cb7ac3ca11d337999b929",
)
DEFAULT_TUSHARE_HTTP_URL = os.getenv("TUSHARE_HTTP_URL", "http://118.89.66.41:8010/")


_pro_client: Any | None = None


def _import_tushare() -> Any:
    try:
        import tushare as ts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("tushare is required. Install it with: pip install tushare") from exc
    return ts


def init_tushare_pro(*, token: str | None = None, http_url: str | None = None, force: bool = False) -> Any:
    """Initialize the shared Tushare Pro client.

    Use this function everywhere instead of calling ``ts.pro_api`` directly.
    Environment variables can override local defaults:

    - TUSHARE_TOKEN
    - TUSHARE_HTTP_URL
    """
    global _pro_client
    if _pro_client is not None and not force:
        return _pro_client

    ts = _import_tushare()
    token = token or DEFAULT_TUSHARE_TOKEN
    http_url = http_url or DEFAULT_TUSHARE_HTTP_URL
    pro = ts.pro_api(token)
    pro._DataApi__http_url = http_url
    _pro_client = pro
    return pro


def pro_bar(*, ts_code: str, limit: int = 3, **kwargs: Any) -> Any:
    """Fetch bar data with the shared Tushare client."""
    ts = _import_tushare()
    pro = init_tushare_pro()
    return ts.pro_bar(api=pro, ts_code=ts_code, limit=limit, **kwargs)


def smoke_test() -> None:
    """Small connectivity test matching the agreed calling convention."""
    ts = _import_tushare()
    pro = init_tushare_pro(force=True)
    df = pro.index_basic(limit=5)
    print(df)
    df = ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3)
    print(df)


if __name__ == "__main__":
    smoke_test()
