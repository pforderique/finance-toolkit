from setuptools import setup, find_packages

setup(
    name="screener",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "npyscreen>=4.10.5",
        "pydantic>=2.11.7",
        "python-dotenv>=1.1.0",
        "redis>=6.2.0",
        "requests>=2.32.3",
        "setuptools>=68.0.0"
    ],
    extras_require={
        "dev": [
            "pytest>=8.4.1",
            "pytest-cov>=6.2.1",
            "coverage>=7.9.2",
        ],
    },
)
