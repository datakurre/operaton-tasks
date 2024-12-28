help:
	@grep -Eh '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' | uniq

INDEX_URL ?= https://pypi.python.org/simple
INDEX_HOSTNAME ?= pypi.python.org

export PYTHONPATH=$(PWD)/src

MODULE := operaton.tasks
APP := operaton-tasks

build:  ## Build application
	devenv build outputs.python.app

env:  ## Build and link the Python virtual environment
	ln -s $(shell devenv build outputs.python.virtualenv) env

check:  ## Run static analysis checks
	black --check src tests
	isort -c src tests
	flake8 src
	MYPYPATH=$(PWD)/stubs mypy --show-error-codes --strict src tests

clean:  ## Remove build artifacts and temporary files
	devenv gc
	$(RM) -r env htmlcov .devenv

devenv-up:  ## Start background services
	devenv processes up -d

devenv-attach:  ## Attach to background services monitor
	devenv shell -- process-compose attach

devenv-down:  ## Stop background services
	devenv processes down

devenv-test: ## Run all test and checks with background services
	devenv test

format:  ## Format the codebase
	treefmt

shell:  ## Start an interactive development shell
	@devenv shell

show:  ## Show build environment information
	@devenv info

test: check test-pytest  ## Run all tests and checks

test-coverage: htmlcov  ## Generate HTML coverage reports

test-pytest:  ## Run unit tests with pytest
	pytest --cov=$(MODULE) tests

watch: .env  ## Start the application in watch mode
	$(APP) heartbeat.py -- --reload

watch-mypy:  ## Continuously run mypy for type checks
	find src tests -name "*.py"|MYPYPATH=$(PWD)/stubs entr mypy --show-error-codes --strict src tests

watch-pytest:  ## Continuously run pytest
	find src tests -name "*.py"|entr pytest tests

watch-tests:  ## Continuously run all tests
	  $(MAKE) -j watch-mypy watch-pytest

###

.coverage: test

htmlcov: .coverage
	coverage html

define _env_script
cat << EOF > .env
ENGINE_REST_BASE_URL="http://localhost:8080/engine-rest"
ENGINE_REST_AUTHORIZATION="Basic ZGVtbzpkZW1v"
EOF
endef
export env_script = $(value _env_script)
.env: ; @ eval "$$env_script"

devenv-%:  ## Run command in devenv shell
	devenv shell -- $(MAKE) $*

nix-%:  ## Run command in devenv shell
	devenv shell -- $(MAKE) $*

FORCE:
