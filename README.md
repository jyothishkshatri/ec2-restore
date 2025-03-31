# EC2 Restore Tool

A command-line tool for restoring EC2 instances from AMIs, with support for both full instance restore and volume-level restore.

## Features

- Full instance restore from AMI
- Volume-level restore from AMI
- Preserves network interfaces and private IPs
- Interactive CLI with rich progress indicators
- Detailed restoration reports
- Support for multiple instances
- Backup of instance metadata before restore

## Installation

```bash
pip install ec2-restore
```

## Configuration

Create a `config.yaml` file with your AWS credentials and restore settings:

```yaml
aws:
  profile: default  # AWS profile name
  region: us-west-2  # AWS region

restore:
  max_amis: 10  # Maximum number of AMIs to show
  log_file: logs/ec2_restore.log  # Log file path
  log_level: INFO  # Logging level
```

## Usage

### Full Instance Restore

```bash
ec2-restore --instance-id i-1234567890abcdef0
```

### Volume Restore

```bash
ec2-restore --instance-id i-1234567890abcdef0 --restore-type volume
```

### Restore by Instance Name

```bash
ec2-restore --instance-name my-instance
```

### Restore Multiple Instances

```bash
ec2-restore --instance-ids i-1234567890abcdef0,i-0987654321fedcba0
```

## Options

- `--instance-id`: EC2 instance ID to restore
- `--instance-name`: EC2 instance name (tag) to restore
- `--instance-ids`: Comma-separated list of EC2 instance IDs to restore
- `--config`: Path to configuration file (default: config.yaml)

## Development

1. Clone the repository:
```bash
git clone https://github.com/yourusername/ec2-restore.git
cd ec2-restore
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install development dependencies:
```bash
pip install -e ".[dev]"
```

## License

This project is licensed under the MIT License - see the LICENSE file for details. 