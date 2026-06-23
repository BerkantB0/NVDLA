SHELL := /usr/bin/env bash

PYTHON ?= python3
PATCHED_NVDLA_SW ?= .work/nvdla-sw-patched
export PYTHONPATH := $(CURDIR)/tools:$(PYTHONPATH)

.DEFAULT_GOAL := help

.PHONY: help doctor lock-check xsa-audit unit sources sources-heavy \
        patch-prepare patch-apply patch-status patch-format patch-check \
        workloads abi-check \
        vp-reference vp-toolchain vp-kernel vp-rootfs vp-kmod vp-test \
        petalinux-smoke petalinux-kmod test report clean

help:
	@printf '%s\n' \
	  'NVDLA PetaLinux driver-correctness framework' \
	  '' \
	  'Fast gates:' \
	  '  make doctor          Check WSL/Docker/PetaLinux tooling' \
	  '  make lock-check      Validate pinned environment metadata' \
	  '  make xsa-audit       Verify the checked-in XSA hardware facts' \
	  '  make unit            Run Python unit tests' \
	  '  make vp-reference    Boot the stock NVDLA VP to the login prompt' \
	  '  make workloads       Generate deterministic workload inputs/goldens' \
	  '  make abi-check       Compare KMD/UMD ioctl headers after source fetch' \
	  '  make test            Run the default fast regression gate' \
	  '' \
	  'Build lanes:' \
	  '  make sources         Fetch pinned nvdla/sw sources only' \
	  '  make sources-heavy   Also fetch pinned linux-xlnx and Buildroot' \
	  '  make patch-apply     Apply patches/nvdla-sw into .work/nvdla-sw-patched' \
	  '  make patch-check     Verify patch queue applies and run checkpatch if available' \
	  '  make patch-format    Regenerate patches from the patched worktree commits' \
	  '  make vp-toolchain    Build/check the pinned Buildroot VP cross compiler' \
	  '  make vp-kernel       Build the modern VP kernel (requires heavy sources)' \
	  '  make vp-rootfs       Build the modern VP rootfs (requires heavy sources)' \
	  '  make vp-kmod         Build opendla.ko against the VP kernel' \
	  '  make petalinux-kmod  Build opendla.ko in a PetaLinux project' \
	  '' \
	  'Reports:' \
	  '  make report          Summarize artifacts into artifacts/latest-report.md'

doctor:
	@scripts/doctor.sh

lock-check:
	@$(PYTHON) -m nvdla_test_framework lock-check --lock repro.lock.json --xsa NVDLA_FPGA_wrapper.xsa

xsa-audit:
	@mkdir -p artifacts
	@$(PYTHON) -m nvdla_test_framework xsa-audit --lock repro.lock.json --xsa NVDLA_FPGA_wrapper.xsa --out artifacts/xsa-audit.json

unit:
	@$(PYTHON) -m unittest discover -s tests -v

sources:
	@scripts/fetch_sources.sh nvdla-sw

sources-heavy:
	@scripts/fetch_sources.sh all

patch-prepare:
	@scripts/nvdla_patch_queue.sh prepare

patch-apply:
	@scripts/nvdla_patch_queue.sh apply

patch-status:
	@scripts/nvdla_patch_queue.sh status

patch-format:
	@scripts/nvdla_patch_queue.sh format

patch-check:
	@scripts/nvdla_patch_queue.sh check

workloads:
	@mkdir -p artifacts/workloads
	@$(PYTHON) -m nvdla_test_framework workload-generate --out artifacts/workloads

abi-check: patch-apply
	@mkdir -p artifacts
	@$(PYTHON) -m nvdla_test_framework abi-check --source $(PATCHED_NVDLA_SW) --out artifacts/abi-check.json

vp-reference:
	@scripts/vp_smoke.sh reference

vp-toolchain:
	@scripts/vp_build.sh toolchain

vp-kernel:
	@scripts/vp_build.sh kernel

vp-rootfs:
	@scripts/vp_build.sh rootfs

vp-kmod:
	@scripts/vp_build.sh kmod

vp-test:
	@$(PYTHON) -m nvdla_test_framework vp-test --lane "$${LANE:-reference}" --lock repro.lock.json

petalinux-smoke:
	@scripts/petalinux_smoke.sh

petalinux-kmod:
	@scripts/petalinux_kmod.sh

test: doctor lock-check unit xsa-audit vp-reference petalinux-smoke

report:
	@mkdir -p artifacts
	@$(PYTHON) -m nvdla_test_framework report --artifacts artifacts --out artifacts/latest-report.md

clean:
	@rm -rf artifacts
