FROM python:3.13-bookworm AS dev

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 1000 --shell /bin/bash fish \
    && chown -R fish:fish /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
USER fish

CMD ["sleep", "infinity"]

# 构建开发镜像时
# ffmpeg 和 curl 会被安装到 /usr/bin
# 一个虚拟环境会被投入到 /opt/venv，并以 requirements.txt 安装环境
# ENV PATH="/opt/venv/bin:$PATH" 将指明从虚拟环境中查找 Python 解释器
# 名为 fish 的用户会被创建

# 当然，有必要换清华镜像源，不然构建很慢

FROM python:3.13-slim-bookworm AS prod

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 1000 --shell /bin/bash fish

WORKDIR /app
COPY --chown=fish:fish . .
USER fish

CMD ["python", "src/main.py"]

# 构建生产镜像时
# ffmpeg 会被安装到 /usr/bin
# 环境会被直接安装到全局 /usr/local/lib/python3.13/site-packages
# 名为 fish 的用户会被创建
# 当前的整个工作区将被复制