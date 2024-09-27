INDEX_URL ?= https://pypi.python.org/simple
INDEX_HOSTNAME ?= pypi.python.org

SRCS := $(shell find src -name "*.py")

NIX_OPTIONS ?= --accept-flake-config --no-pure-eval

.PHONY: all
all: build

build: $(SRCS)
	$(RM) build
	nix build $(NIX_OPTIONS) -o build

.PHONY: clean
clean:
	rm -rf .cache env result

.PHONY: coverage
coverage: htmlcov

.PHONY: venv
venv:
	nix build $(NIX_OPTIONS) .#env -o venv

.PHONY: format
format:
	nix fmt flake.nix
	black src tests
	isort src tests

.PHONY: check
check: src/operaton/__init__.py
	black --check src tests
	isort -c src tests
	flake8 src
	MYPYPATH=$(PWD)/stubs mypy --show-error-codes --strict src tests

.PHONY: watch
watch:
	operaton-tasks --reload

.PHONY: watch_mypy
watch_mypy: src/operaton/__init__.py
	find src tests -name "*.py"|MYPYPATH=$(PWD)/stubs entr mypy --show-error-codes --strict src tests

.PHONY: watch_pytest
watch_pytest:
	find src tests -name "*.py"|entr pytest tests

.PHONY: watch_tests
watch_tests:
	  $(MAKE) -j watch_mypy watch_pytest

.PHONY: pytest
pytest:
	pytest --cov=operaton.tasks tests

.PHONY: test
test: check pytest

env:
	nix build $(NIX_OPTIONS) .#env -o env

.PHONY: shell
shell:
	nix develop $(NIX_OPTIONS)

###

nix-%:
	nix develop $(NIX_OPTIONS) --command $(MAKE) $*

.coverage: test

htmlcov: .coverage
	coverage html

# error: Skipping analyzing "operaton.tasks"
src/operaton/__init__.py:
	touch $@
