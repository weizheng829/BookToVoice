FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 运行时挂载点（volume 会在启动时覆盖，这里仅占位避免目录缺失）
RUN mkdir -p /app/input /app/output /app/data

EXPOSE 3033

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3033"]
