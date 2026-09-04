.PHONY: help test install demo strategies dna run calibrate app useradd users

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
	@echo ""
	@echo "  The app (login, settings, start):"
	@echo "    make useradd USER=owner ROLE=OWNER"
	@echo "    make app CONFIG=session.json DATA=bars.csv"

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

# The app: sign in, configure, start. The engine comes up halted -- starting is
# an action somebody takes after looking at the settings, not a default.
app:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli serve --login \
		--config $(abspath $(CONFIG)) --data $(abspath $(DATA)) \
		--operators $(abspath $(or $(OPERATORS),operators.json)) $(FLAGS)

useradd:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli useradd $(USER) \
		--role $(or $(ROLE),OPERATOR) \
		--operators $(abspath $(or $(OPERATORS),operators.json))

users:
	@cd $(API) && PYTHONPATH=src python3 -m elyon.cli users \
		--operators $(abspath $(or $(OPERATORS),operators.json))
