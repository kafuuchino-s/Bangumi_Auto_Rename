FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.10-slim-bullseye

ENV TZ=Asia/Shanghai
WORKDIR /Bangumi_Auto_Rename

# 先复制依赖文件，利用缓存
COPY requirements_docker.txt .

RUN echo "deb https://mirrors.aliyun.com/debian/ bullseye main non-free contrib" > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        git python-is-python3 unrar \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libatspi2.0-0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libasound2 \
        libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements_docker.txt -i https://mirrors.aliyun.com/pypi/simple/

# 只复制源码
COPY src/ ./src/

EXPOSE 5999
CMD ["python3", "-m", "src.start"]