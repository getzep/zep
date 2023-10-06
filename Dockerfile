FROM golang:1.21.2-bookworm AS BUILD
LABEL authors="danielchalef"

RUN mkdir /app
WORKDIR /app
COPY . .
RUN go mod download && make build

FROM debian:bookworm-slim AS RUNTIME
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=BUILD /app/out/bin/zep /app/

EXPOSE 8000
ENTRYPOINT ["/app/zep", "--config", "/app/config.yaml"]
