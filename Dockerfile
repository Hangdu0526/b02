FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_ROOT=/input
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends wine wine64 xvfb util-linux \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/program/solution
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY data/inputs/*.dat /input/
RUN chmod +x run.sh run_client.sh && test -f data/official/serve.exe && command -v setsid
ENTRYPOINT ["/app/program/solution/run.sh"]
CMD ["--task", "all", "--test", "--out-dir", "/output"]
