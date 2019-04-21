"""
breakfast: An AST based refactoring tool for Python.

Copyright (c) 2015-2016
Eric Casteleijn, <thisfred@gmail.com>
"""
import os
import re

from setuptools import setup


def find_version(*file_paths):
    """Get version from python file."""
    with open(os.path.join(os.path.dirname(__file__), *file_paths)) as version_file:
        contents = version_file.read()
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", contents, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


setup(
    name="breakfast",
    version=find_version("breakfast/__init__.py"),
    author="Eric Casteleijn",
    author_email="thisfred@gmail.com",
    description="Python refactoring tool",
    license="BSD",
    keywords="refactoring",
    url="http://github.com/thisfred/breakfast",
    packages=["breakfast"],
    long_description="",  # open('README.md').read(),
    classifiers=[
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
    ],
)
