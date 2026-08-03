#!/usr/bin/env bash
# OTP graph 数据准备（幂等）：下载 OSM pbf → 裁剪京都/宇治范围 → 就位 GTFS。
#
# OSM 源：openstreetmap.fr 的 kansai extract（geofabrik 同数据，镜像更快），
#   用 osmium 裁剪出京都/宇治 bbox（graph 更小、构建更快更省内存）。
#   下载慢时可走代理：https_proxy=http://127.0.0.1:7890 ./download.sh（curl 认标准环境变量）。
# GTFS：京都市営バス/地下鉄的 GTFS-JP 只发布在公共交通オープンデータセンター
#   （ODPT），需要免费注册拿 consumerKey：
#     1. https://developer.odpt.org/ 注册，获得 key
#     2. 下载 zip 放到 otp/data/（本目录 data/*.zip），build.sh 会自动纳入 graph
#   例：curl -L -o otp/data/Kyoto_City_Bus_GTFS.zip \
#     'https://api.odpt.org/api/v4/files/odpt/KyotoMunicipalTransportation/Kyoto_City_Bus_GTFS.zip?date=current&acl:consumerKey=YOUR_KEY'
#   没有 GTFS 时 graph 只含路网（walk/drive 真实耗时），公共交通换乘降级（已知的
#   覆盖缺口：宇治的 JR 奈良线/京阪宇治线、京都市营巴士/地铁均不可查）。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p src data

SRC=src/kansai.osm.pbf
SRC_URL=https://download.openstreetmap.fr/extracts/asia/japan/kansai-latest.osm.pbf
OUT=data/Kyoto.osm.pbf
BBOX=135.65,34.78,136.05,35.10  # 京都市+宇治（含余量）
# stefda/osmium-tool 只发布 latest，按 digest pin 住保证可重复
OSMIUM_IMAGE=stefda/osmium-tool:latest@sha256:d2321d0e926f77ead7547b4b35f5cf98d9fd74043673cecc4fc2bb7cce06ff63

if [ -s "$OUT" ]; then
  echo "skip: $OUT 已存在"
else
  if [ -s "$SRC" ]; then
    echo "skip 下载: $SRC 已存在"
  else
    echo "下载 OSM: $SRC_URL"
    curl -fL --retry 3 -o "$SRC" "$SRC_URL"
  fi
  echo "裁剪 $BBOX → $OUT"
  docker run --rm -v "$PWD:/data" "$OSMIUM_IMAGE" \
    osmium extract -b "$BBOX" "/data/$SRC" -o "/data/$OUT" --overwrite
fi

GTFS_COUNT=$(find data -maxdepth 1 -name '*.zip' | wc -l | tr -d ' ')
if [ "$GTFS_COUNT" = "0" ]; then
  echo "警告: data/ 下没有 GTFS zip——graph 将只含路网（公共交通换乘降级）。"
  echo "      获取方式见本脚本头部注释（ODPT 注册）。"
fi
ls -lh data/
