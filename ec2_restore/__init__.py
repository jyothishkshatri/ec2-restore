"""
EC2 Restore Tool

A powerful tool for restoring EC2 instances from AMIs with advanced features and detailed reporting.
"""

from ec2_restore.modules.cli import cli
from ec2_restore.modules.display import display_volume_changes, display_instance_changes
from ec2_restore.modules.restore_manager import RestoreManager
from ec2_restore.modules.aws_client import AWSClient

__version__ = "1.1.0"

__all__ = [
    'cli',
    'display_volume_changes',
    'display_instance_changes',
    'RestoreManager',
    'AWSClient',
] 