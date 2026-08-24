.PHONY: help test install demo

help:
	@echo "ELYON QUANT"
	@echo "  make install  Install dev dependencies"
	@echo "  make test     Run the test suite"
	@echo "  make demo     Run the end-to-end pipeline demo"

install:
	pip3 install --quiet pytest

test:
	cd services/platform-api && python3 -m pytest

demo:
	cd services/platform-api && PYTHONPATH=src python3 demo_pipeline.py
