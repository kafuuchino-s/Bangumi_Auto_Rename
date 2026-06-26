# 基础镜像默认走 Docker Hub 官方源（CI / 海外环境直连）。
# 国内本地构建若拉 Docker Hub 受 TLS 干扰，可覆盖：
#   docker build --build-arg NODE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22-bookworm-slim \
#                --build-arg PYTHON_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---- stage 1: 前端静态导出构建器 ----
# Next.js 16 静态导出（output: export）→ frontend/out，由运行期 FastAPI 同端口托管。
# node 22：Pi sidecar 依赖（@earendil-works/pi-tui / undici@8）要求 node >=22.19，本地开发亦用 node 22。
FROM ${NODE_IMAGE} AS frontend

WORKDIR /build

# 先复制依赖清单利用缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 复制源码并构建静态导出
COPY frontend/ ./
RUN npm run build
# 产物在 /build/out


# ---- stage 2: 静态 ffmpeg/ffprobe（替代 apt ffmpeg 全家桶 579M）----
# mwader/static-ffmpeg 的 ffmpeg/ffprobe 是 glibc 静态链接单文件（各 ~134M），bookworm 可直接跑。
# ffprobe：主流程 local_fact_surface.py 探媒体元数据（时长/分辨率），只需 demuxer。
# ffmpeg：ffsubsync 字幕对齐调用（音频提取/变换，123 处调用 vs ffprobe 27 处），缺它对齐跑不起来。
# 相比 apt ffmpeg（带 libllvm 112M + libflite/libmfx/libgl/codec2...）净省 ~312M。
FROM mwader/static-ffmpeg:latest AS ffprobe-src


# ---- stage 3: Python 依赖构建器（编译工具链留此层，不进最终镜像）----
# scrapling/cryptography 等含 C 扩展需 build-essential 编译。编译完只拷 site-packages，
# gcc/llvm/libstdc++-dev 等 ~266M 编译工具链留在本 stage，运行期镜像不带。
FROM ${PYTHON_IMAGE} AS py-deps

WORKDIR /Bangumi_Auto_Rename

COPY requirements_docker.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libc6-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements_docker.txt \
    # 删死重（默认 browser_enabled=false 用不到浏览器抓取）：
    #   - patchright（138M，scrapling 的反检测浏览器后端，非硬依赖）
    #   - playwright/driver（133M Node 浏览器驱动；scrapling.fetchers 顶层 import 只需 playwright Python 层）
    && rm -rf /usr/local/lib/python3.12/site-packages/patchright \
              /usr/local/lib/python3.12/site-packages/playwright/driver \
    # 清运行期冗余（在 COPY 到最终镜像前删，真正省体积；分层 rm 不省下层）：
    #   - __pycache__（43M .pyc，运行期自动重建）、包内 tests（4M）、pip 自身（13M，装完依赖不再需要）
    #   不删 *.dist-info（curl_cffi 等用 importlib.metadata.version() 查版本，删了破坏 import）
    && find /usr/local/lib/python3.12/site-packages -type d \( -name '__pycache__' -o -name tests -o -name test \) -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/local/lib/python3.12/site-packages/pip; \
    true


# ---- stage 4: 运行时镜像 ----
# Python 3.12 + Node 22（Pi sidecar）+ 静态 ffprobe + unrar。
# Python 3.12：对齐本地 .venv（3.12.9）与代码用到的 3.11+ 特性（typing.Self / TypeAlias）。
FROM ${PYTHON_IMAGE}

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /Bangumi_Auto_Rename

# 运行期系统依赖（不含 ffmpeg/编译工具链/系统 python3.11；ffprobe 用静态二进制，python 用 /usr/local 的 3.12）。
# bookworm slim 默认源只有 main，开 contrib/non-free/non-free-firmware（unrar 在 non-free；字幕解压 .rar 需要）。
# 不装 python-is-python3（会拉系统 python3.11 全家桶 27M 死重；代码用 python3/`python -m` 走 /usr/local 的 3.12）。
RUN sed -i 's/Components: main$/Components: main contrib non-free non-free-firmware/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        unrar \
        ca-certificates \
        # scrapling DynamicFetcher 浏览器运行时库（默认 browser_enabled=false 用不到，保留以便启用浏览器抓取）
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libatspi2.0-0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libasound2 \
        libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /Bangumi_Auto_Rename/tests /Bangumi_Auto_Rename/data

# 静态 ffmpeg/ffprobe（从 stage 2）：
#   ffprobe — 主流程探媒体元数据；
#   ffmpeg — ffsubsync 字幕对齐调用（缺它对齐失败）。
COPY --from=ffprobe-src /ffprobe /usr/local/bin/ffprobe
COPY --from=ffprobe-src /ffmpeg /usr/local/bin/ffmpeg
RUN chmod +x /usr/local/bin/ffprobe /usr/local/bin/ffmpeg

# Python 依赖：从 py-deps 拷编译好的 site-packages（不含 gcc/llvm，已删 playwright/driver）。
COPY --from=py-deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

# Node.js 22 运行时（Pi sidecar 必需）：从 frontend stage 复制官方 node 镜像的二进制 + npm。
# node 是真实二进制可直接 COPY；npm/npx 在原镜像里是软链，COPY 单文件会解引用破坏其内部 require，
# 故只复制 npm 模块目录 + 在 RUN 里手动重建软链。
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf /usr/local/bin/node /usr/local/bin/nodejs

# Python 源码 + 配置页 AI 测试样例
COPY src/ ./src/
COPY tests/example_test_case.json tests/example_expected.json ./tests/

# 前端静态导出产物（从 stage 1 拷贝，约 1.9M）
COPY --from=frontend /build/out/ ./frontend/out/

# Pi sidecar 运行时：根 package.json 依赖装到仓库根（runner cwd=REPO_ROOT 经 node_modules 解析）
# 同 RUN 内清 npm 缓存 + node_modules 冗余（.map/.d.ts/.md/docs，~130M），同层删才真正省镜像体积。
COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
    && rm -rf /root/.npm \
    && find /Bangumi_Auto_Rename/node_modules -type f \( -name '*.map' -o -name '*.d.ts' -o -name '*.md' \) -delete 2>/dev/null; \
    find /Bangumi_Auto_Rename/node_modules -type d -name docs -exec rm -rf {} + 2>/dev/null; \
    true

# Pi sidecar 脚本 + 合同 skills + 工具扩展
COPY tools/pi_*.mjs ./tools/
COPY .pi/skills/ ./.pi/skills/
COPY .pi/extensions/ ./.pi/extensions/

# 凭据不进镜像：.pi/agent/auth.json 排除，运行时经 config（ai_api_key /
# rename_local_bangumi_pi_api_key）→ 环境变量 BAR_PI_CASE_AGENT_API_KEY 注入 sidecar。
# data/ 运行期挂载（config/task/record/ai_analysis/pi_case_agent 等）。

# 非 root 运行：分发场景安全合规，部分 NAS 强制非 root。
# data/ 是运行期挂载点（首启空），build 时 chown 让挂载侧默认属主对齐；
# 运行期若挂载了属主不同的 data/，需在挂载侧调整权限（docker run -v 时由宿主侧决定）。
RUN groupadd -r app && useradd -r -g app -d /Bangumi_Auto_Rename -s /usr/sbin/nologin app \
    && chown -R app:app /Bangumi_Auto_Rename
USER app

# 健康检查：用镜像自带的 python3 urllib 调 /health，不依赖 curl（slim 镜像未装）。
# /health 是 src/web.py 注册的轻量端点，不触发业务/队列，仅返回进程存活信号。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://localhost:5999/health',timeout=3).read()" || exit 1

EXPOSE 5999
CMD ["python3", "-m", "src.start"]
