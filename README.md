# Finance Toolkit

A set of financial tools and research studies to aid in portfolio management,
stock screening, and trading strategies.

### [Screener](./screener/README.md)

A stock screener tool to fetch and store Moriningstar data. Contains:
* **update_stocks.py**: utility & script to monitor daily price changes and
ratings update. Can send alert emails with actions per stock.
* **terminal UI:** for quickly screening stocks based on their fair market
value, discount/premium, and ratings.

## Installation
Using a virtual environment is reccommended. This is a pip installable python
package that will install needed dependencies accross all tools.
```bash
# Use a virtual environment
python -m venv ~/envs/screener
source ~/envs/screener/bin/activate

# Install the finance-toolkit package
pip install -e .
```

## Contributing
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
coverage run -m pytest && coverage html && open htmlcov/index.html
```