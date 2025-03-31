# EC2 Instance Restore Tool - Usage Guide

## Overview

The EC2 Instance Restore Tool provides functionality for restoring EC2 instances from AMIs with two main restoration types:
- Full Instance Restore: Creates a new instance while preserving network interfaces and configuration
- Volume-Level Restore: Restores specific volumes from an AMI to an existing instance

## Installation

1. Install the package:
```bash
pip install -r requirements.txt
```

2. Configure AWS credentials in one of these ways:
   - Using AWS CLI: `aws configure`
   - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
   - AWS credentials file
   - IAM role (for EC2 instances)

## Configuration

The tool uses a `config.yaml` file for configuration:

```yaml
aws:
  profile: default  # AWS profile name
  region: us-east-1  # AWS region

restore:
  max_amis: 5  # Number of AMIs to show in selection
  backup_metadata: true  # Backup instance metadata before restore
  log_level: INFO  # Logging level
  log_file: ec2_restore.log  # Log file path
```

## Basic Usage

### 1. Restore by Instance ID

```bash
ec2-restore restore --instance-id i-1234567890abcdef0
```

### 2. Restore by Instance Name

```bash
ec2-restore restore --instance-name my-instance-name
```

### 3. Restore Multiple Instances

```bash
ec2-restore restore --instance-ids i-1234567890abcdef0,i-0987654321fedcba0
```

## Restore Types

### Full Instance Restore

This creates a new instance while preserving:
- Network interface (ENI) and private IP
- IAM roles
- Tags and metadata
- Instance type and placement
- Security groups

Example output:
```
Starting EC2 instance restore process...
✓ Instance metadata backed up to: backups/instance_i-1234567890_20240330_123456.json
✓ Selected AMI: ami-0123456789abcdef0
✓ Network interface preserved: eni-0123456789abcdef0
✓ New instance created: i-0987654321fedcba0
✓ Instance tags restored
✓ Restore completed in 245.67 seconds
```

### Volume-Level Restore

Allows restoring specific volumes from an AMI while:
- Creating snapshots of existing volumes
- Supporting rollback in case of failures
- Preserving instance configuration
- Handling volume attachments automatically

Example output:
```
Starting volume restore process...
✓ Instance metadata backed up to: backups/instance_i-1234567890_20240330_123456.json
✓ Creating snapshot of /dev/sda1: snap-0123456789abcdef0
✓ Creating new volume from AMI: vol-0987654321fedcba0
✓ Detaching current volume: vol-1234567890abcdef0
✓ Attaching new volume: vol-0987654321fedcba0
✓ Restore completed in 180.45 seconds
```

## Volume Selection

When performing a volume restore, you'll see a table of available volumes:

```
Available Volumes:
┌───────┬──────────┬───────────┬───────────┬─────────────────────┐
│ Index │ Device   │ Size (GB) │ Type      │ Delete on Term.     │
├───────┼──────────┼───────────┼───────────┼─────────────────────┤
│ 1     │ /dev/sda1│ 100       │ gp3       │ true               │
│ 2     │ /dev/sdf │ 500       │ io2       │ false              │
└───────┴──────────┴───────────┴───────────┴─────────────────────┘
```

Select volumes by:
- Entering comma-separated indices: `1,2`
- Entering 'all' to select all volumes
- Entering 'q' or 'quit' to cancel

## AMI Selection

The tool shows recent AMIs associated with the instance:

```
Available AMIs:
┌───────┬──────────────────────────┬─────────────────┬────────────────────┐
│ Index │ AMI ID                   │ Creation Date   │ Description        │
├───────┼──────────────────────────┼─────────────────┼────────────────────┤
│ 1     │ ami-0123456789abcdef0   │ 2024-03-29     │ Backup 2024-03-29  │
│ 2     │ ami-0987654321fedcba0   │ 2024-03-28     │ Backup 2024-03-28  │
└───────┴──────────────────────────┴─────────────────┴────────────────────┘
```

## Restore Reports

After each restore operation, a detailed report is generated:

```json
{
  "timestamp": "2024-03-30T12:34:56.789",
  "restore_type": "volume",
  "instance_name": "my-instance",
  "instance_id": "i-1234567890abcdef0",
  "changes": {
    "volumes": {
      "/dev/sda1": {
        "previous": "vol-1234567890abcdef0",
        "current": "vol-0987654321fedcba0"
      }
    },
    "state": {
      "previous": "running",
      "current": "running"
    }
  }
}
```

## Safety Features

1. Automatic Backup
   - Instance metadata backed up before changes
   - Volume snapshots created before replacement
   - Original instance state preserved

2. Error Handling
   - Automatic rollback on failures
   - Resource cleanup
   - Detailed error messages
   - Progress tracking

3. State Preservation
   - Network interface preservation
   - Instance state restoration
   - Tag preservation
   - Security group preservation

## Logging

Detailed logs are written to the configured log file (default: ec2_restore.log):

```
2024-03-30 12:34:56 INFO Starting EC2 instance restore process
2024-03-30 12:34:57 INFO Creating snapshot of volume vol-1234567890abcdef0
2024-03-30 12:35:30 INFO Created new volume vol-0987654321fedcba0
2024-03-30 12:36:15 INFO Restore completed successfully
```

## Common Issues and Solutions

1. Instance Not Found
   ```
   Error: Instance i-1234567890abcdef0 not found
   Solution: Verify instance ID and AWS region
   ```

2. Volume Attachment Failure
   ```
   Error: Volume attachment point already in use
   Solution: Tool automatically attempts force detachment
   ```

3. Network Interface Issues
   ```
   Error: Cannot detach primary network interface
   Solution: Tool preserves ENI during full restore
   ```

## Best Practices

1. Always review the volume changes before confirming
2. Keep note of the backup files and restore reports
3. Monitor the logs for detailed operation status
4. Use instance name tags for easier identification
5. Maintain regular AMI backups of critical instances

## Environment Variables

- `AWS_PROFILE`: Override configured AWS profile
- `AWS_DEFAULT_REGION`: Override configured region
- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key