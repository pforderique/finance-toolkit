# Finance Toolkit

A set of financial tools and research studies to aid in portfolio management,
stock screening, and trading strategies.


# Contributing
Install dev dependencies with 
```bash
pip install -e .'[dev]'
```
and run tests using `pytest` at the root. To run simple coverage for a package like screener, 
```bash
pytest --cov=screener --cov-report=term-missing
```
or directly use `coverage` to generate an html report:
```
coverage run -m pytest
coverage report -m
coverage html
```