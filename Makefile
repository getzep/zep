GOCMD=go
GOTEST=$(GOCMD) test
GOVET=$(GOCMD) vet
BINARY_NAME=zep
VERSION?=0.0.0
SERVICE_PORT?=
DOCKER_REGISTRY?=
EXPORT_RESULT?=false # for CI please set EXPORT_RESULT to true

BUILD_VAR=

PACKAGE := github.com/getzep/zep/config
VERSION := $(shell git describe --tags --always --abbrev=0 --match='v[0-9]*.[0-9]*.[0-9]*' 2> /dev/null | sed 's/^.//')
COMMIT_HASH := $(shell git rev-parse --short HEAD)
BUILD_TIMESTAMP := $(shell date '+%Y-%m-%dT%H:%M:%S%z')

LDFLAGS = -X '${PACKAGE}.Version=${VERSION}' \
          -X '${PACKAGE}.CommitHash=${COMMIT_HASH}' \
          -X '${PACKAGE}.BuildTime=${BUILD_TIMESTAMP}'

GREEN  := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
WHITE  := $(shell tput -Txterm setaf 7)
CYAN   := $(shell tput -Txterm setaf 6)
RESET  := $(shell tput -Txterm sgr0)

.PHONY: all test build

all: test build

## Build:
build: ## Build your project
	mkdir -p ./out/bin
	$(BUILD_VAR) $(GOCMD) build -ldflags="${LDFLAGS}" -o ./out/bin/$(BINARY_NAME)

clean: ## Remove build related file
	rm -f $(BINARY_NAME)
	rm -f ./junit-report.xml checkstyle-report.xml ./coverage.xml ./profile.cov yamllint-checkstyle.xml

## Test:
## don't parallelize testing as there are sideefects to some DB tests
test: ## Run project tests
	$(GOTEST) -tags=testutils -race -p 1 ./...

coverage: ## Run the tests of the project and export the coverage
	$(GOTEST) -cover -covermode=count -coverprofile=profile.cov ./...
	$(GOCMD) tool cover -func profile.cov
ifeq ($(EXPORT_RESULT), true)
	GO111MODULE=off go get -u github.com/AlekSi/gocov-xml
	GO111MODULE=off go get -u github.com/axw/gocov/gocov
	gocov convert profile.cov | gocov-xml > coverage.xml
endif

## Generate swagger docs:
swagger:
	swag i -g pkg/server/routes.go -o docs
	swag fmt

## Lint:
lint:
	golangci-lint run --deadline=90s --sort-results -c golangci.yaml

## Run the dev stack docker compose setup. This exposes DB and NLP services
## for local development. This does not start the Zep service.
dev:
	docker compose -f docker-compose.dev.yaml up db nlp

## Go Watch for web development
## https://github.com/mitranim/gow
watch:
	ZEP_DEVELOPMENT=true ZEP_SERVER_HOST=localhost gow -e=go,mod,html,js,css -i=node_modules run .

# Build web assets
web:
	cd pkg/web && npx tailwindcss -i static/input.css -o static/output.css

## Docker:
docker-build: ## Use the dockerfile to build the container
	DOCKER_BUILDKIT=1 docker build --rm --tag $(BINARY_NAME) .

docker-release: ## Release the container with tag latest and version
	docker tag $(BINARY_NAME) $(DOCKER_REGISTRY)$(BINARY_NAME):latest
	docker tag $(BINARY_NAME) $(DOCKER_REGISTRY)$(BINARY_NAME):$(VERSION)
	# Push the docker images
	docker push $(DOCKER_REGISTRY)$(BINARY_NAME):latest
	docker push $(DOCKER_REGISTRY)$(BINARY_NAME):$(VERSION)

## Help:
help: ## Show this help.
	@echo ''
	@echo 'Usage:'
	@echo '  ${YELLOW}make${RESET} ${GREEN}<target>${RESET}'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} { \
		if (/^[a-zA-Z_-]+:.*?##.*$$/) {printf "    ${YELLOW}%-20s${GREEN}%s${RESET}\n", $$1, $$2} \
		else if (/^## .*$$/) {printf "  ${CYAN}%s${RESET}\n", substr($$1,4)} \
		}' $(MAKEFILE_LIST)
