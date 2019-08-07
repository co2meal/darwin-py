import setuptools

with open("README.md", "r") as f:
    long_description = f.read()

setuptools.setup(
    name="darwin",
    version="0.0.1",
    author="V7",
    author_email="info@v7labs.com",
    description="Command line interface for Darwin",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/v7labs/darwin-cli",
    install_requires=[
        "argcomplete",
        "docutils",
        "humanize",
        "pyyaml>=5.1",
        "requests",
        "sh",
        "tqdm",
        "factory_boy",
    ],
    packages=["darwin"],
    entry_points={"console_scripts": ["darwin=darwin.cli:main"]},
    classifiers=["Programming Language :: Python :: 3"],
)
