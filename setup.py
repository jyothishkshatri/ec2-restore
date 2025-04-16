from setuptools import setup, find_namespace_packages
import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Create necessary directories
os.makedirs("ec2_restore/config", exist_ok=True)

# Copy config.yaml.example to package directory
if os.path.exists("config.yaml.example"):
    with open("config.yaml.example", "r") as src, open("ec2_restore/config/config.yaml.example", "w") as dst:
        dst.write(src.read())

setup(
    name="ec2-restore",
    version="1.1.0",
    author="Jyothish Kshatri",
    author_email="kshatri.jyothish3@gmail.com",
    description="A tool for restoring EC2 instances from AMIs",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jyothishkshatri/ec2-restore",
    packages=find_namespace_packages(include=['ec2_restore', 'ec2_restore.*']),
    include_package_data=True,
    package_data={
        'ec2_restore': ['config/*.yaml', 'modules/*.py'],
    },
    data_files=[
        ('ec2_restore/config', ['config.yaml.example']),
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "click>=8.0.0",
        "boto3>=1.26.0",
        "rich>=10.0.0",
        "pyyaml>=6.0.0",
    ],
    entry_points={
        "console_scripts": [
            "ec2-restore=ec2_restore.modules.cli:cli",
        ],
    },
) 