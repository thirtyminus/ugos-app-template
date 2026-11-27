APP_ID      := com.example.demoapp
GOTOOLCHAIN := go1.21.13
GOFLAGS     := -trimpath -ldflags="-s -w"
ARCH        ?= amd64
BUILD       ?=

.PHONY: help build build-amd64 build-arm64 check pack verify clean dev

help:
	@echo "用法:"
	@echo "  make build            编译 linux amd64/arm64 后端二进制"
	@echo "  make check            校验 project.yaml 与目录结构"
	@echo "  make pack BUILD=17    清空 build_dir 后打包并自动校验（BUILD 必须递增）"
	@echo "  make verify           校验 build_dir 下已有 upk"
	@echo "  make dev              本地起后端（macOS 调试用，端口 $(PORT)）"
	@echo "  make clean            清理构建产物"

build: build-amd64 build-arm64

build-amd64:
	mkdir -p rootfs_amd64/bin
	cd backend && GOTOOLCHAIN=$(GOTOOLCHAIN) CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build $(GOFLAGS) -o ../rootfs_amd64/bin/demoapp_serv .

build-arm64:
	mkdir -p rootfs_arm64/bin
	cd backend && GOTOOLCHAIN=$(GOTOOLCHAIN) CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build $(GOFLAGS) -o ../rootfs_arm64/bin/demoapp_serv .

check:
	ugcli check

# 打包前必须清空 build_dir：残留中间产物会产出畸形 upk
pack: build check
	@test -n "$(BUILD)" || { echo "缺少 BUILD：make pack BUILD=<递增的构建号>"; exit 1; }
	rm -rf build_dir
	ugcli pack --arch $(ARCH) --build $(BUILD)
	python3 tools/verify_upk.py

verify:
	python3 tools/verify_upk.py

PORT ?= 21010
dev:
	cd backend && go run . --port=$(PORT)

clean:
	rm -rf build_dir
	rm -f rootfs_amd64/bin/demoapp_serv rootfs_arm64/bin/demoapp_serv
