"""第 2 步：anitabi 详细数据灌库脚本的离线测试（真实响应形状 fixture，不触网）。"""

import time

import httpx

from app.ingest_seichi import AnitabiCrawler, build_pack, point_to_pack

# 真实 anitabi /bangumi/115908/lite 响应（裁剪）
LITE_115908 = {
    "id": 115908,
    "cn": "吹响吧！上低音号",
    "title": "響け！ユーフォニアム",
    "city": "宇治市",
}

# 真实 /points/detail 响应（裁剪，含各种边界形态）
POINTS_115908 = [
    {"id": "7gs3o1mm", "cn": "宇治桥", "name": "宇治橋",
     "image": "https://image.anitabi.cn/points/115908/7gs3o1mm.jpg?plan=h160",
     "ep": 2, "s": 809, "geo": [34.8929, 135.8065],
     "origin": "Anitabi@卜卜口", "originURL": "https://anitabi.cn/"},
    {"id": "qys7k4", "name": "大吉山展望台 蓝调",  # 无中文名 → 回退原名
     "image": "https://image.anitabi.cn/user/0/bangumi/115908/points/qys7k4.jpg?plan=h160",
     "ep": 8, "s": 1131, "geo": [34.8926, 135.8125],
     "origin": "Google Maps", "originURL": "https://www.google.com/maps/d/viewer?mid=x"},
    {"id": "qys7j2", "name": "天ケ瀬ダム", "ep": "OST",  # ep 为字符串
     "geo": [34.8808, 135.828], "origin": "Google Maps", "originURL": "https://x"},
    {"id": "nogeo", "name": "无坐标点", "ep": 1},  # 无 geo → 丢弃
]


def test_point_to_pack_字段映射():
    pack = point_to_pack(POINTS_115908[0], "宇治市")

    assert pack == {
        "id": "7gs3o1mm",
        "name": "宇治桥",
        "lat": 34.8929,
        "lng": 135.8065,
        "image": "https://image.anitabi.cn/points/115908/7gs3o1mm.jpg?plan=h160",
        "ep": 2,
        "ep_seconds": 809,
        "origin": "Anitabi@卜卜口",
        "origin_url": "https://anitabi.cn/",
        "area": "宇治市",
    }


def test_point_to_pack_边界形态():
    assert point_to_pack(POINTS_115908[1], "宇治市")["name"] == "大吉山展望台 蓝调"
    assert point_to_pack(POINTS_115908[2], "宇治市")["ep"] == "OST"
    assert point_to_pack(POINTS_115908[3], "宇治市") is None  # 无坐标丢弃


def test_build_pack_完整文档():
    pack = build_pack(115908, LITE_115908, POINTS_115908)

    assert pack["subject_id"] == 115908
    assert pack["work"] == "吹响吧！上低音号"
    assert pack["city"] == "宇治市"
    assert len(pack["points"]) == 3  # 无坐标点被过滤


def test_fetch_pack_404返回None(monkeypatch):
    crawler = AnitabiCrawler(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        )
    )
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)  # 跳过真实限速

    assert crawler.fetch_pack(999999) is None


def test_fetch_pack_完整链路(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lite"):
            return httpx.Response(200, json=LITE_115908)
        return httpx.Response(200, json=POINTS_115908)

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)  # 跳过真实限速
    crawler = AnitabiCrawler(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    pack = crawler.fetch_pack(115908)

    assert pack is not None
    assert len(pack["points"]) == 3
    assert pack["points"][0]["name"] == "宇治桥"


def test_localize_images_下载并改写为本地URL(monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)  # 跳过真实限速
    crawler = AnitabiCrawler(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"jpeg-bytes"))
        )
    )
    pack = build_pack(115908, LITE_115908, POINTS_115908)

    n = crawler.localize_images(pack, tmp_path / "images")

    # 有 image 的两个点被本地化；无 image 的点跳过
    assert n == 2
    assert pack["points"][0]["image"] == "/api/seichi-images/115908/7gs3o1mm.jpg"
    assert (tmp_path / "images/115908/7gs3o1mm.jpg").read_bytes() == b"jpeg-bytes"
    assert pack["points"][2]["image"] is None

    # 幂等：文件已存在则不再请求，只改写字段
    def boom(request):
        raise AssertionError("不应再发请求")

    crawler2 = AnitabiCrawler(client=httpx.Client(transport=httpx.MockTransport(boom)))
    pack2 = build_pack(115908, LITE_115908, POINTS_115908)
    assert crawler2.localize_images(pack2, tmp_path / "images") == 2
    assert pack2["points"][0]["image"].startswith("/api/seichi-images/")


def test_localize_images_下载失败保留远程URL(monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    crawler = AnitabiCrawler(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(502))
        )
    )
    pack = build_pack(115908, LITE_115908, POINTS_115908)
    remote = pack["points"][0]["image"]

    assert crawler.localize_images(pack, tmp_path / "images") == 0
    assert pack["points"][0]["image"] == remote  # 保留远程 URL，不产出本地 404
