FROM python:3.12-alpine

WORKDIR /app

COPY server.py .

RUN mkdir -p /data

EXPOSE 5000

CMD ["python", "server.py"]
