FROM python:3.12-alpine

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

RUN apk add --no-cache tzdata ca-certificates

COPY . .

CMD ["python", "bot.py"]
