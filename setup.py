from setuptools import find_packages, setup

setup(
    name="ci-system",
    version="0.1.0",
    packages=find_packages(
        include=[
            "ci_common",
            "ci_common.*",
            "ci_persistence",
            "ci_persistence.*",
            "ci_controller",
            "ci_controller.*",
            "ci_server",
            "ci_server.*",
            "ci_client",
            "ci_client.*",
            "ci_admin",
            "ci_admin.*",
        ]
    ),
    install_requires=[
        "requests>=2.31.0",
        "fastapi>=0.104.0",
        "uvicorn>=0.24.0",
        "python-multipart>=0.0.6",
        "aiosqlite>=0.19.0",
        "click>=8.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-xdist>=3.3.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "ci=ci_client.cli:main",
            "ci-controller=ci_controller.__main__:main",
            "ci-admin=ci_admin.cli:cli",
        ],
    },
    python_requires=">=3.8",
)
