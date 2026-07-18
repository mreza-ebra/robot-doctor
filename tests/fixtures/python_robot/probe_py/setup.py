from setuptools import setup

setup(
    name="probe_py",
    version="0.1.0",
    packages=["probe_py"],
    entry_points={"console_scripts": ["probe_node = probe_py.node:main"]},
)
