FROM golang:1.22.5-bookworm AS BUILD

RUN mkdir /app
WORKDIR /app
COPY . .
WORKDIR /app/src
RUN go mod download
WORKDIR /app
RUN make -f Makefile.ce build

FROM debian:bookworm-slim AS RUNTIME
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=BUILD /app/out/bin/zep /app/
# Ship with default config that can be overridden by ENV vars
COPY zep.yaml /app/

EXPOSE 8000
ENTRYPOINT ["/app/zep"]
