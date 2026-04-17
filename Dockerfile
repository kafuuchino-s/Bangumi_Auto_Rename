FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.10-slim-bullseye

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /Bangumi_Auto_Rename

# 先复制依赖文件，利用缓存
COPY requirements_docker.txt .

RUN printf '%s\n' \
        'deb http://deb.debian.org/debian bullseye main contrib non-free' \
        'deb http://deb.debian.org/debian-security bullseye-security main contrib non-free' \
        'deb http://deb.debian.org/debian bullseye-updates main contrib non-free' \
        > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        git python-is-python3 unrar ffmpeg build-essential libc6-dev \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libatspi2.0-0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libasound2 \
        libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements_docker.txt \
    && mkdir -p /Bangumi_Auto_Rename/tests /Bangumi_Auto_Rename/data

# 只复制运行时需要的源码与配置页 AI 测试样例
COPY src/ ./src/
COPY tests/example_test_case.json tests/example_expected.json ./tests/

# 当前镜像按默认配置使用 scrapling 非浏览器抓取。
# 若后续启用 subtitle_auto_fetch_browser_enabled=true，需要额外构建带浏览器运行时的镜像。
EXPOSE 5999
CMD ["python3", "-m", "src.start"]
