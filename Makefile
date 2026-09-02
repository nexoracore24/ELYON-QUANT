.PHONY: help test install demo strategies dna run calibrate

API := services/platform-api

help:
	@echo "ELYON QUANT"
	@echo ""
	@echo "  make install     Install dev dependencies"
	@echo "  make test        Run the test suite"
	@echo "  make demo        Run the end-to-end pipeline demo"
	@echo ""
	@echo "  make strategies  List the strategy catalog and its tiers"
	@echo "  make dna         List the Market DNA profiles"
	@echo ""
	@echo "  Running a session:"
	@echo "    make config > session.json"
	@echo "    make run CONFIG=session.json DATA=bars.csv"
	@echo "    make calibrate DATA=bars.csv STRATEGY=SIX_PILLARS"

install:
	pip3 install --quiet pytest

test:
	cd $(API) && python3 -m pytest

demo:
	cd $(API) && PYTHONPATH=src python3 demo_pipeline.py

strategies:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli strategies

dna:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli dna $(SYMBOL)

config:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli config --symbol $(or $(SYMBOL),EURUSD)

run:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli run \
		--config $(abspath $(CONFIG)) --data $(abspath $(DATA)) $(FLAGS)

calibrate:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli calibrate \
		--data $(abspath $(DATA)) --strategy $(or $(STRATEGY),SIX_PILLARS) \
		--sample $(or $(SAMPLE),IN_SAMPLE)
