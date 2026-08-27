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
    """进度事件总线：按会话分发 received/thinking/planning/done/error 事件。

    publish 同时写入 backlog（晚订阅的连接/测试也能拿到历史）并推给在线
    订阅者；backlog 每会话封顶 BACKLOG_LIMIT 条防内存膨胀。
    每条事件带会话内单调递增的 id，SSE 重连时凭 Last-Event-ID 只回放
    没见过的，避免流式增量被重复上屏。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backlog: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=BACKLOG_LIMIT)
        )
        self._subscribers: dict[str, list[queue.Queue]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)

    def publish(self, conversation_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        """发布事件：分配递增 id，先入 backlog，再推送给该会话的所有订阅队列。"""
        with self._lock:
            self._seq[conversation_id] += 1
            item = {"id": self._seq[conversation_id], "event": event, "data": data or {}}
            self._backlog[conversation_id].append(item)
            subscribers = list(self._subscribers[conversation_id])
        for q in subscribers:
            q.put(item)

    def subscribe(
        self, conversation_id: str, last_event_id: int | None = None
    ) -> "queue.Queue[dict[str, Any]]":
        """订阅某会话：先回放 backlog 中的历史事件，再持续接收新事件。

        last_event_id 非空时只回放 id 更大的事件（SSE 重连幂等）。"""
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            for item in self._backlog[conversation_id]:
                if last_event_id is None or item["id"] > last_event_id:
                    q.put(item)
            self._subscribers[conversation_id].append(q)
        return q


event_bus = EventBus()  # 进程级单例（多实例部署时需换外部 broker，见模块注释）
