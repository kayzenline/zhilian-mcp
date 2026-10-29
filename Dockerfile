FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ /app/
ENV DATA_DIR=/data PORT=18064 TZ=Asia/Shanghai
VOLUME /data
EXPOSE 18064
CMD ["python","server.py"]
