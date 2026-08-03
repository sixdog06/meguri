"""进度事件总线：内存版，按会话分发。

Agent Loop 处理消息时发布事件（received / thinking / done），
SSE 端点订阅并推送给前端。单进程开发环境够用；
多进程/多实例部署时需换成外部 broker（后续 ticket）。

backlog 让晚于消息处理才订阅的连接（含测试）也能拿到已发生的事件；
每会话只保留最近 100 条（deque maxlen），防止内存无限增长。
"""

import queue
import threading
from collections import defaultdict, deque
from typing import Any

BACKLOG_LIMIT = 100


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backlog: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=BACKLOG_LIMIT)
        )
        self._subscribers: dict[str, list[queue.Queue]] = defaultdict(list)

    def publish(self, conversation_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        item = {"event": event, "data": data or {}}
        with self._lock:
            self._backlog[conversation_id].append(item)
            subscribers = list(self._subscribers[conversation_id])
        for q in subscribers:
            q.put(item)

    def subscribe(self, conversation_id: str) -> "queue.Queue[dict[str, Any]]":
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            for item in self._backlog[conversation_id]:
                q.put(item)
            self._subscribers[conversation_id].append(q)
        return q


event_bus = EventBus()
