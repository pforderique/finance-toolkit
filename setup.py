"""Setup script for the finance-toolkit package."""

import setuptools

setuptools.setup(
    name="finance-toolkit",
    version="0.1.0",
    packages=setuptools.find_packages(),
    install_requires=[
        "google-api-python-client>=2.100.0",
        "npyscreen>=4.10.5",
        "pydantic>=2.11.7",
        "python-dotenv>=1.1.0",
        "redis>=6.2.0",
        "requests>=2.32.3",
        "setuptools>=68.0.0",
        "validators>=0.35.0",
        "typer>=0.9.0",
        "rich>=13.5.2",
        "selenium>=4.21.0",
        "webdriver-manager>=4.0.2",
    ],
    extras_require={
        "dev": [
            "pytest>=8.4.1",
            "pytest-cov>=6.2.1",
            "coverage>=7.9.2",
        ],
    },
)
