#!/usr/bin/env python3
"""
EC2 Instance Restore Tool

A powerful and user-friendly tool for restoring EC2 instances from AMIs with support
for both full instance restoration and volume-level restoration.
"""

from ec2_restore.modules.cli import cli

if __name__ == '__main__':
    cli() 