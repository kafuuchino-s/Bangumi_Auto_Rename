FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.10-slim-bullseye

ENV TZ=Asia/Shanghai

WORKDIR /Bangumi_Auto_Rename

COPY . /Bangumi_Auto_Rename

RUN echo "deb https://mirrors.aliyun.com/debian/ bullseye main non-free contrib" > /etc/apt/sources.list
RUN apt-get update && apt-get install -y git && apt-get install -y python-is-python3
RUN pip install --upgrade pip 
RUN apt-get install -y libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libasound2 libpango-1.0-0 libcairo2
RUN pip install -r requirements_docker.txt -i https://mirrors.aliyun.com/pypi/simple/

EXPOSE 5999

CMD ["sh", "-c", "pwd && ls /Bangumi_Auto_Rename && python3 -m src.start"]