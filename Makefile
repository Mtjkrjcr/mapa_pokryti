PYTHON ?= python3
CONFIG ?= config/config.yaml

.PHONY: venv install prepare-dem compute export all production web clean

venv:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -r requirements.txt

install:
	pip install -r requirements.txt

prepare-dem:
	$(PYTHON) -m src.main prepare-dem --config $(CONFIG)

compute:
	$(PYTHON) -m src.main compute --config $(CONFIG)

export:
	$(PYTHON) -m src.main export --config $(CONFIG)

all:
	$(PYTHON) -m src.main all --config $(CONFIG)

production:
	$(PYTHON) -m src.main all --config config/config_production.yaml

web:
	cd web && $(PYTHON) -m http.server 8080

clean:
	rm -rf tmp/* out/*
