#!/usr/bin/env python3
"""HARA V13 package configuration."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="hara-auto-fill",
    version="13.1.0",
    author="HARA Automation Team",
    author_email="support@example.com",
    description="Template-driven, evidence-grounded HARA analysis",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/example/hara-auto-fill",
    package_dir={"": "src"},
    packages=find_packages("src"),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.12",
    install_requires=[
        "openpyxl>=3.0.0",
        "PyPDF2>=2.0.0",
        "python-docx>=0.8.11",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
        ],
        "test": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "hara-agent=hara_agent.cli:main",
        ],
    },
    include_package_data=True,
)
