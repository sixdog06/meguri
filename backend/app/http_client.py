"""外呼 HTTP 的共享礼貌层：统一 User-Agent、限速、指数退避重试。

灌库脚本（ingest_bangumi / ingest_seichi）与各 live 适配器共用 UA；
polite_call 供灌库脚本做"限速 + 重试"的一致策略（重试耗尽上抛，
由调用方决定降级/跳过）。
"""

import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from curl_cffi.requests.exceptions import RequestException as CurlRequestException

USER_AGENT = "meguri/0.1 (https://github.com/sixdog06/meguri)"
REQUEST_INTERVAL = 0.6  # 秒；≤2 请求/秒
MAX_RETRIES = 3

T = TypeVar("T")


def polite_call(
    fn: Callable[[], T],
    *,
    max_retries: int = MAX_RETRIES,
    interval: float = REQUEST_INTERVAL,
) -> T:
    """限速 + 指数退避重试执行 fn（网络/HTTP/解析错误）；重试耗尽上抛。

    每次调用前 sleep interval（限速）；失败后按 interval * 2^attempt 退避。
    """
    last_error: Exception | None = None
    for attempt in range(max_retries):
        time.sleep(interval)
        try:
            return fn()
        except (httpx.HTTPError, ValueError, CurlRequestException) as exc:
            # curl_cffi 的异常体系独立于 httpx，anitabi 抓取链（含截图下载）
            # 走 curl_cffi，不捕它则网络错误第一次失败就放弃该作品
            last_error = exc
            time.sleep(interval * (2**attempt))
    raise last_error  # type: ignore[misc]
