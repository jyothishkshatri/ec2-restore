# EC2 Instance Restore Tool

A command-line tool for restoring EC2 instances from AMIs, with support for both full instance restoration and volume-level restoration.

## Features

- **Full Instance Restore**
  - Creates a new instance from an AMI
  - Preserves network interface (ENI) and private IP
  - Maintains IAM roles, tags, and other configurations
  - Shows real-time progress and timing information

- **Volume Restore**
  - Restores specific volumes from an AMI
  - Creates snapshots of existing volumes before restoration
  - Supports rollback in case of failures
  - Shows detailed progress and timing for each operation

- **Progress Tracking**
  - Real-time progress indicators
  - Operation timing information
  - Detailed logging of each step
  - Clear success/failure status

- **Safety Features**
  - Automatic metadata backup
  - Volume snapshots before changes
  - Rollback support
  - State preservation

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/ec2-instance-restore.git
cd ec2-instance-restore
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Configuration

1. Create a `config.yaml` file in the project root:
```yaml
aws:
  profile: your-profile-name
  region: your-region

restore:
  max_amis: 5  # Number of recent AMIs to show
  backup_dir: backups  # Directory for storing backups and reports

logging:
  level: INFO
  file: ec2_restore.log
```

2. Configure your AWS credentials:
```bash
aws configure --profile your-profile-name
```

## Usage

### Full Instance Restore

```bash
ec2-restore restore --instance-id i-1234567890abcdef0
```

The tool will:
1. Show the last 5 AMIs for the instance
2. Ask for restore type (full/volume)
3. For full restore:
   - Backup instance metadata
   - Modify network interface to persist
   - Stop and terminate the old instance
   - Create new instance with same configuration
   - Restore all tags and settings
4. Show real-time progress and timing
5. Generate a restoration report

### Volume Restore

```bash
ec2-restore restore --instance-id i-1234567890abcdef0
```

The tool will:
1. Show the last 5 AMIs for the instance
2. Ask for restore type (full/volume)
3. For volume restore:
   - Backup instance metadata
   - Show available volumes from AMI
   - Create snapshots of current volumes
   - Create new volumes from AMI
   - Detach old volumes and attach new ones
4. Show real-time progress and timing
5. Generate a restoration report

## Output and Logging

The tool provides:
- Real-time progress indicators
- Operation timing information
- Detailed logs in `ec2_restore.log`
- Restoration reports in JSON format
- Clear success/failure status

Example output:
```
Starting EC2 instance restore process
Processing 1 instances: i-1234567890abcdef0
[spinner] Getting details for instance i-1234567890abcdef0...
[spinner] Backing up instance metadata...
✓ Instance metadata backed up to: backup_20240321_123456.json (2.34 seconds)
[spinner] Fetching available AMIs...
[spinner] Performing volume restore...
✓ Volume restore completed successfully (45.67 seconds)
[spinner] Generating restoration report...
✓ Restoration report generated: restore_report_20240321_123456.json (1.23 seconds)
✓ Instance i-1234567890abcdef0 processed successfully (49.24 seconds)
✓ EC2 instance restore process completed (49.24 seconds)
```

## Restoration Reports

The tool generates detailed reports showing:
- Timestamp of restoration
- Restore type (full/volume)
- Instance name and IDs
- Volume changes
- State changes
- Duration of operations

Example report:
```json
{
  "timestamp": "2024-03-21T12:34:56.789",
  "restore_type": "volume",
  "instance_name": "my-instance",
  "instance_id": "i-1234567890abcdef0",
  "changes": {
    "volumes": {
      "/dev/sda1": {
        "previous": "vol-1234567890abcdef0",
        "current": "vol-abcdef1234567890"
      }
    },
    "state": {
      "previous": "stopped",
      "current": "running"
    }
  }
}
```

## Error Handling

The tool includes comprehensive error handling:
- Automatic rollback on failure
- Resource cleanup
- State restoration
- Detailed error logging
- User-friendly error messages

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 