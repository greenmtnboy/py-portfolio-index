# type: ignore
import ast
import re

import setuptools

_version_re = re.compile(r"__version__\s+=\s+(.*)")
with open("py_portfolio_index/__init__.py", "rb") as f:
    _match = _version_re.search(f.read().decode("utf-8"))
    if _match is None:
        print("No version found")
        raise SystemExit(1)
    version = str(ast.literal_eval(_match.group(1)))


with open("requirements.txt", "r") as f:
    install_requires = [line.strip().replace("==", ">=") for line in f.readlines()]

setuptools.setup(
    name="py-portfolio-index",
    version=version,
    url="",
    author="",
    description="Build index portfolios.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(
        exclude=[
            "dist",
            "build",
            "*.tests",
            "*.tests.*",
            "tests.*",
            "tests",
            "docs",
            ".github",
            "",
            "examples",
            "local_examples",
            ".vscode",
        ]
    ),
    package_data={
        "": ["*.jinja", "py.typed", "*.csv", "*.json"],
    },
    install_requires=install_requires,
    extras_require={
        "alpaca": ["alpaca-py"],
        "robinhood": ["robin-stocks"],
        "webull": ["webull"],
        "schwab": ["schwab-py"],
    },
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
