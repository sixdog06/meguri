FROM python:3.12-slim

# 构建上下文是仓库根（见 docker-compose.yml），镜像内保持仓库目录结构：
# /meguri/backend/app + /meguri/data —— FileSeichiRepository 以
# 本文件上三级为仓库根解析 data/，该布局下无需改代码。
WORKDIR /meguri/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY data /meguri/data

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
