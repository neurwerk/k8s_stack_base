# k8s_stack_base — Validation & linting tooling
# ==============================================
# Run `make help` to see available targets.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# --- Tool discovery ---
HELM        := $(shell command -v helm 2>/dev/null || echo "")
KUSTOMIZE   := $(shell command -v kustomize 2>/dev/null || echo "")
KUBECONFORM := $(shell command -v kubeconform 2>/dev/null || echo "")
KUBELINTER  := $(shell command -v kube-linter 2>/dev/null || echo "")
PRECOMMIT   := $(shell command -v pre-commit 2>/dev/null || echo "")
PYTHON3     := $(shell command -v python3 2>/dev/null || echo "")
UV          := $(shell command -v uv 2>/dev/null || echo "")

# --- Paths ---
CHART_DIRS     := $(sort $(shell find charts -type f -name Chart.yaml -print | xargs -n1 dirname))
KUSTOMIZE_DIRS := $(sort $(shell find releases -type f -name kustomization.yaml -print | xargs -n1 dirname))

# Charts with committed dependency locks. Validation never updates these files.
HAS_DEPS := $(sort $(shell find charts -type f -name Chart.lock -print | xargs -n1 dirname))
HELM_LINT_VALUES := $(CURDIR)/tests/validation/helm-lint-values.yaml

# --- Colors ---
RED    := \033[31m
GREEN  := \033[32m
YELLOW := \033[33m
CYAN   := \033[36m
RESET  := \033[0m

.PHONY: help tools helm-lint helm-lint-only helm-validate kustomize-validate kube-linter chart-check security-check platform-check release-manifest release-check release-notes check live-acceptance live-postgres-acceptance pre-commit-install helm-deps deps-verify

help: ## Show this help
	@printf "$(CYAN)Available targets:$(RESET)\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-22s$(RESET) %s\n", $$1, $$2}'

tools: ## Check that required CLIs are installed
	@printf "$(CYAN)Checking required tools...$(RESET)\n"
	@errors=0; \
	for tool in "helm" "kustomize" "kubeconform" "kube-linter" "python3" "uv"; do \
		path=$$(command -v "$$tool" 2>/dev/null || true); \
		if [ -z "$$path" ]; then \
			printf "  $(RED)✗$(RESET) $$tool — not found\n"; \
			errors=$$((errors + 1)); \
		else \
			case "$$tool" in \
				helm) version=$$("$$tool" version --short 2>/dev/null) ;; \
				kustomize) version=$$("$$tool" version 2>/dev/null) ;; \
				kubeconform) version=$$("$$tool" -v 2>/dev/null) ;; \
				kube-linter) version=$$("$$tool" version 2>/dev/null) ;; \
				python3) version=$$("$$tool" --version 2>/dev/null) ;; \
				uv) version=$$("$$tool" --version 2>/dev/null) ;; \
			esac; \
			printf "  $(GREEN)✓$(RESET) $$tool — $$version\n"; \
		fi \
	done; \
	if [ "$$errors" -gt 0 ]; then \
		printf "\n$(RED)❌ $$errors tool(s) missing. Install them before running checks.$(RESET)\n"; \
		exit 1; \
	fi; \
	printf "$(GREEN)All required tools are installed.$(RESET)\n"

helm-deps: ## Rebuild Helm chart dependencies from committed lock files
	@printf "$(CYAN)Rebuilding Helm dependencies...$(RESET)\n"
	@for dir in $(HAS_DEPS); do \
		printf "  helm dep build %s\n" "$$dir"; \
		cd "$(CURDIR)/$$dir" && $(HELM) dependency build --skip-refresh 2>&1 | tail -1; \
	done
	@printf "$(GREEN)Done.$(RESET)\n"

helm-lint: deps-verify helm-lint-only ## Verify deps then lint all charts

helm-lint-only: ## Lint all charts (without updating deps)
	@printf "$(CYAN)Linting all charts...$(RESET)\n"
	@errors=0; \
	for dir in $(CHART_DIRS); do \
		if [ ! -d "$(CURDIR)/$$dir/templates" ]; then printf "  $(GREEN)✓$(RESET) %s — no local templates\\n" "$$dir"; continue; fi; \
		output=$$(cd "$(CURDIR)/$$dir" && $(HELM) lint --strict . --values "$(HELM_LINT_VALUES)" 2>&1) || { printf "  $(RED)✗$(RESET) %s — FAILED\\n" "$$dir"; echo "$$output"; errors=$$((errors + 1)); continue; }; \
		printf "  $(GREEN)✓$(RESET) %s — PASSED\n" "$$dir"; \
	done; \
	if [ "$$errors" -gt 0 ]; then \
		printf "\n$(RED)❌ $$errors chart(s) failed linting.$(RESET)\n"; \
		exit 1; \
	fi; \
	printf "$(GREEN)All charts passed linting.$(RESET)\n"

helm-validate: deps-verify ## Render & validate all charts against Kubernetes schema
	@printf "$(CYAN)Rendering and validating all charts...$(RESET)\n"
	@tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	errors=0; \
	for dir in $(CHART_DIRS); do \
		tmpfile="$$tmpdir/rendered.yaml"; \
		if ! $(HELM) template "$$(basename "$$dir")" "$(CURDIR)/$$dir" --skip-tests --values "$(HELM_LINT_VALUES)" > "$$tmpfile"; then \
			printf "  $(RED)✗$(RESET) %s — render failed\n" "$$dir"; \
			errors=$$((errors + 1)); \
		elif ! grep -Eq '^kind:[[:space:]]+' "$$tmpfile"; then \
			printf "  $(RED)✗$(RESET) %s — rendered zero resources\n" "$$dir"; \
			errors=$$((errors + 1)); \
		elif $(KUBECONFORM) -strict -summary -ignore-missing-schemas "$$tmpfile"; then \
			printf "  $(GREEN)✓$(RESET) %s — valid\n" "$$dir"; \
		else \
			printf "  $(RED)✗$(RESET) %s — schema validation failed\n" "$$dir"; \
			errors=$$((errors + 1)); \
		fi; \
	done; \
	if [ "$$errors" -gt 0 ]; then \
		printf "\n$(RED)❌ $$errors chart(s) failed schema validation.$(RESET)\n"; \
		exit 1; \
	fi; \
	printf "$(GREEN)All charts passed validation.$(RESET)\n"

kustomize-validate: deps-verify ## Build and validate root Kustomizations
	@printf "$(CYAN)Building and validating root Kustomizations...$(RESET)\n"
	@if [ -z "$(KUSTOMIZE)" ]; then printf "$(RED)kustomize is required$(RESET)\n"; exit 1; fi
	@for dir in releases/namespaces releases/infrastructure releases/applications; do \
		$(KUSTOMIZE) build --load-restrictor LoadRestrictionsNone "$(CURDIR)/$$dir" | $(KUBECONFORM) -strict -summary -ignore-missing-schemas; \
	done

kube-linter: deps-verify ## Lint rendered charts with kube-linter (best practices)
	@printf "$(CYAN)Running kube-linter on all charts...$(RESET)\n"
	@if [ -z "$(KUBELINTER)" ]; then printf "$(RED)kube-linter is required$(RESET)\n"; exit 1; fi; \
	if [ ! -f "$(CURDIR)/.kube-linter.yaml" ]; then \
		printf "$(RED).kube-linter.yaml not found — skipping.$(RESET)\n"; \
		exit 0; \
	fi; \
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	errors=0; \
	for dir in $(CHART_DIRS); do \
		tmpfile="$$tmpdir/rendered.yaml"; \
		if ! $(HELM) template "$$(basename "$$dir")" "$(CURDIR)/$$dir" --skip-tests --values "$(HELM_LINT_VALUES)" > "$$tmpfile"; then \
			printf "  $(RED)✗$(RESET) %s — render failed\n" "$$dir"; \
			errors=$$((errors + 1)); \
		elif ! grep -Eq '^kind:[[:space:]]+' "$$tmpfile"; then \
			printf "  $(RED)✗$(RESET) %s — rendered zero resources\n" "$$dir"; \
			errors=$$((errors + 1)); \
		elif $(KUBELINTER) lint --config "$(CURDIR)/.kube-linter.yaml" "$$tmpfile"; then \
			printf "  $(GREEN)✓$(RESET) %s — no issues\n" "$$dir"; \
		else \
			printf "  $(RED)✗$(RESET) %s — issues found\n" "$$dir"; \
			errors=$$((errors + 1)); \
		fi; \
	done; \
	if [ "$$errors" -gt 0 ]; then exit 1; fi
	@printf "$(GREEN)kube-linter finished.$(RESET)\n"

deps-verify: ## Verify committed dependency archives without changing them
	@for dir in $(HAS_DEPS); do \
		if ! $(HELM) dependency list "$(CURDIR)/$$dir" >/dev/null; then \
			printf "$(RED)Dependency verification failed: %s$(RESET)\n" "$$dir"; exit 1; \
		fi; \
	done

security-check: ## Run offline MCP security policy tests
	@if [ -z "$(PYTHON3)" ]; then \
		printf "$(RED)python3 not found. Install it before running security checks.$(RESET)\n"; \
		exit 1; \
	fi
	@$(PYTHON3) -m unittest discover -s tests/security/static -p 'test_*.py' -v

chart-check: ## Run chart-specific rendered contract tests
	@if [ -z "$(PYTHON3)" ]; then \
		printf "$(RED)python3 not found. Install it before running chart checks.$(RESET)\n"; \
		exit 1; \
	fi
	@$(PYTHON3) -m unittest discover -s tests/charts -p 'test_*.py' -v

platform-check: ## Verify the generated platform release contract
	@if [ -z "$(UV)" ]; then printf "$(RED)uv is required$(RESET)\n"; exit 1; fi
	@PLATFORM_RELEASE_TEST_TAG="$(TAG)" $(UV) run --frozen python -m unittest discover -s tests/platform -p 'test_*.py' -v

release-manifest: ## Regenerate the platform release manifest
	@if [ -z "$(UV)" ]; then printf "$(RED)uv is required$(RESET)\n"; exit 1; fi
	@$(UV) run --frozen python scripts/platform_release.py generate

release-check: platform-check ## Verify release evidence; pass TAG=vX.Y.Z for tag consistency
	@$(UV) run --frozen python scripts/platform_release.py check $(if $(TAG),--tag "$(TAG)",)

release-notes: ## Write release notes; pass OUTPUT=<path>
	@if [ -z "$(OUTPUT)" ]; then printf "$(RED)OUTPUT is required$(RESET)\n"; exit 1; fi
	@$(UV) run --frozen python scripts/platform_release.py notes --output "$(OUTPUT)"

check: tools helm-lint helm-validate kustomize-validate kube-linter chart-check security-check platform-check ## Run full local validation suite

live-acceptance: ## Run explicitly opted-in live AgentGateway acceptance (not part of check)
	@if [ -z "$(PYTHON3)" ]; then \
		printf "$(RED)python3 not found. Install it before running live acceptance.$(RESET)\n"; \
		exit 1; \
	fi
	@$(PYTHON3) tests/live/agentgateway/acceptance.py

live-postgres-acceptance: ## Run explicitly opted-in disposable PostgreSQL acceptance (not part of check)
	@if [ -z "$(PYTHON3)" ]; then \
		printf "$(RED)python3 not found. Install it before running live acceptance.$(RESET)\n"; \
		exit 1; \
	fi
	@$(PYTHON3) -u tests/live/postgres/acceptance.py

pre-commit-install: ## Install pre-commit hooks
	@if [ -n "$(PRECOMMIT)" ]; then \
		$(PRECOMMIT) install; \
		printf "$(GREEN)pre-commit hooks installed.$(RESET)\n"; \
	else \
		printf "$(RED)pre-commit not found. Install it first.$(RESET)\n"; \
		exit 1; \
	fi
