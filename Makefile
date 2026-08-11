.PHONY: help test install

help:
	@echo "ELYON QUANT"
	@echo "  make install  Install dev dependencies"
	@echo "  make test     Run the test suite"

install:
	pip3 install --quiet pytest

test:
	cd services/platform-api && python3 -m pytest
