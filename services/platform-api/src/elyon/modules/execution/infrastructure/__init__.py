"""Adapters to real venues.

The OMS core is agnostic of broker and of mode; these are the anti-corruption
layers that make one venue look like the contract. Nothing here makes a trading
decision -- an adapter's whole job is to answer three questions truthfully.
"""
