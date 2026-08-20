#!/usr/bin/env python3
"""
HARA自动化填写技能安装文件
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="hara-auto-fill",
    version="1.0.0",
    author="HARA Automation Team",
    author_email="support@example.com",
    description="基于GB/T 34590.3标准的HARA（危害分析和风险评估）自动化工具",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/example/hara-auto-fill",
    package_dir={"": "src", "scripts": "scripts"},
    packages=find_packages("src") + find_packages(where=".", include=["scripts", "scripts.*"]),
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
        "pdfplumber>=0.9.0",
        "python-docx>=0.8.11",
        "numpy>=1.24.0",
        "matplotlib>=3.7.0",
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
            "hara-analyze=scripts.main_executor:main",
            "hara-example=scripts.example:main",
        ],
    },
    include_package_data=True,
    package_data={
        "hara_auto_fill": [
            "assets/*",
            "references/*",
            "templates/*",
        ],
    },
)
