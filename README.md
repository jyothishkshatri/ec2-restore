# EC2 Restore Tool

A powerful command-line tool for restoring EC2 instances from AMIs with advanced features and detailed reporting.

## Features

- **Full Instance Restore**: Create a new instance from an AMI while preserving network configuration
- **Volume-Level Restore**: Restore specific volumes from an AMI to an existing instance
- **Detailed Progress Tracking**: Real-time progress updates with rich console output
- **Comprehensive Reporting**: Generate detailed restoration reports
- **Network Configuration Preservation**: Maintain private IP addresses and network settings
- **Systems Manager Integration**: Execute post-restore commands using AWS Systems Manager
- **Interactive SSM Sessions**: Run commands on EC2 instances without performing a restore
- **Instance Metadata Backup**: Automatic backup of instance metadata before restoration
- **Volume Change Visualization**: Clear display of volume changes before and after restoration
- **Instance Change Tracking**: Detailed comparison of instance configurations
- **Error Handling & Rollback**: Robust error handling with automatic rollback capabilities

## Installation

```bash
pip install ec2-restore
```

## Version Information

To check the installed version of the tool:

```bash
ec2-restore --version
```

This will display the version in the format: `ec2-restore, version X.X.X`

## Configuration

Create a `config.yaml` file in your working directory:

```yaml
aws:
  profile: default  # AWS profile to use. If not specified, will use default profile
  region: us-east-1  # Default region, can be overridden by environment variable

restore:
  max_amis: 5  # Number of AMIs to show in selection
  backup_metadata: true  # Whether to backup instance metadata before restoration
  log_level: INFO  # Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  log_file: ec2_restore.log  # Log file path

systems_manager:
  enabled: true  # Whether to run Systems Manager commands after restore
  commands:  # List of commands to run after instance restore
    - name: "check os version"  # Friendly name for the command
      command: "cat /etc/os-release"  # The actual command to run
      timeout: 300  # Command timeout in seconds
      wait_for_completion: true  # Whether to wait for command completion
    - name: "check disk size"
      command: "df -kh"
      timeout: 300
      wait_for_completion: true
  document_name: "AWS-RunShellScript"  # Default SSM document to use
  output_s3_bucket: ""  # Optional S3 bucket for command output
  output_s3_prefix: ""  # Optional S3 prefix for command output
```

## Usage Examples

### Full Instance Restore

```bash
# Restore by instance ID
ec2-restore restore --instance-id i-1234567890abcdef0

# Restore by instance name
ec2-restore restore --instance-name my-instance

# Restore multiple instances
ec2-restore restore --instance-ids i-1234567890abcdef0,i-0987654321fedcba0
```

Example output for full instance restore:
```
✓ Getting details for instance i-1234567890abcdef0...
✓ Backing up instance metadata...
✓ Fetching available AMIs...

Available AMIs:
┌───────┬────────────────────┬───────────────────────┬────────────────────────────┐
│ Index │ AMI ID            │ Creation Date        │ Description                │
├───────┼────────────────────┼───────────────────────┼────────────────────────────┤
│ 1     │ ami-1234567890    │ 2024-03-15T10:30:00Z │ Production backup          │
│ 2     │ ami-0987654321    │ 2024-03-14T15:45:00Z │ Daily backup              │
└───────┴────────────────────┴───────────────────────┴────────────────────────────┘

Instance Changes Before Restore:
┌────────────────────┬────────────────────┬────────────────────┬──────────┐
│ Property           │ Previous Value     │ New Value          │ Status   │
├────────────────────┼────────────────────┼────────────────────┼──────────┤
│ Instance ID        │ i-1234567890abcdef0│ Pending            │ Pending  │
│ Instance Name      │ prod-server-1      │ prod-server-1      │ Preserved │
│ Instance Type      │ t2.micro           │ t2.micro           │ Preserved │
│ Private IP         │ 10.0.1.100         │ Pending            │ Pending  │
└────────────────────┴────────────────────┴────────────────────┴──────────┘

✓ Performing full instance restore...
✓ New instance created with ID: i-0987654321fedcba0
✓ Waiting for instance to be fully available...
✓ Getting new instance details...
✓ Getting AMI volumes for report...

Instance Changes After Restore:
┌────────────────────┬────────────────────┬────────────────────┬──────────┐
│ Property           │ Previous Value     │ New Value          │ Status   │
├────────────────────┼────────────────────┼────────────────────┼──────────┤
│ Instance ID        │ i-1234567890abcdef0│ i-0987654321fedcba0│ Changed  │
│ Instance Name      │ prod-server-1      │ prod-server-1      │ Preserved │
│ Instance Type      │ t2.micro           │ t2.micro           │ Preserved │
│ Private IP         │ 10.0.1.100         │ 10.0.1.100         │ Preserved │
└────────────────────┴────────────────────┴────────────────────┴──────────┘
```

### Volume Restore

```bash
# Restore specific volumes
ec2-restore restore --instance-id i-1234567890abcdef0 --restore-type volume
```

Example output for volume restore:
```
✓ Getting details for instance i-1234567890abcdef0...
✓ Backing up instance metadata...
✓ Fetching available AMIs...

Available AMIs:
┌───────┬────────────────────┬───────────────────────┬────────────────────────────┐
│ Index │ AMI ID            │ Creation Date        │ Description                │
├───────┼────────────────────┼───────────────────────┼────────────────────────────┤
│ 1     │ ami-1234567890    │ 2024-03-15T10:30:00Z │ Production backup          │
└───────┴────────────────────┴───────────────────────┴────────────────────────────┘

Current Volume Configuration:
┌───────┬──────────┬───────────┬──────────┬──────────────────────┐
│ Index │ Device   │ Size (GB) │ Type     │ Delete on Termination │
├───────┼──────────┼───────────┼──────────┼──────────────────────┤
│ 1     │ /dev/sda1│ 8         │ gp2      │ True                 │
│ 2     │ /dev/sdb │ 20        │ gp3      │ False                │
└───────┴──────────┴───────────┴──────────┴──────────────────────┘

✓ Performing volume restore...
✓ Volume restore completed successfully

Volume Changes:
┌──────────┬────────────────────┬────────────────────┬──────────┐
│ Device   │ Previous Volume ID │ New Volume ID      │ Status   │
├──────────┼────────────────────┼────────────────────┼──────────┤
│ /dev/sda1│ vol-1234567890    │ vol-0987654321     │ Changed  │
│ /dev/sdb │ vol-2345678901    │ vol-1098765432     │ Changed  │
└──────────┴────────────────────┴────────────────────┴──────────┘
```

### Options

- `--instance-id`: EC2 instance ID to restore
- `--instance-name`: EC2 instance name (tag) to restore
- `--instance-ids`: Comma-separated list of EC2 instance IDs to restore
- `--restore-type`: Type of restore (full or volume)
- `--config`: Path to configuration file (default: config.yaml)
- `--version` or `-v`: Display version information

### Running SSM Commands

You can run Systems Manager (SSM) commands on EC2 instances without performing a restore operation. This is useful for executing maintenance tasks, checking instance status, or running scripts on your instances.

```bash
# Run interactive SSM session
ec2-restore ssm --instance-id i-1234567890abcdef0

# Run a single SSM command
ec2-restore ssm --instance-id i-1234567890abcdef0 --command "df -kh"

# Specify a custom timeout for the command
ec2-restore ssm --instance-id i-1234567890abcdef0 --command "yum update -y" --timeout 600

# Use a custom SSM document
ec2-restore ssm --instance-id i-1234567890abcdef0 --document "AWS-RunPowerShellScript"
```

Example output for interactive SSM session:
```
✓ Verifying instance i-1234567890abcdef0...

Starting interactive SSM session for instance: i-1234567890abcdef0
You have the following predefined commands available:

Systems Manager Commands
┌─────────────────┬───────────────────┬─────────┬─────┐
│ Name            │ Command           │ Timeout │ Wait │
├─────────────────┼───────────────────┼─────────┼─────┤
│ check os version│ cat /etc/os-release│ 300s    │ Yes │
│ check disk size │ df -kh            │ 300s    │ Yes │
└─────────────────┴───────────────────┴─────────┴─────┘

Do you want to run one of these predefined commands? [y/n]: y
Select a command to run [check os version/check disk size/all/quit]: check os version

Executing command on i-1234567890abcdef0
Command: cat /etc/os-release

Command Output:
NAME="Amazon Linux"
VERSION="2"
ID="amzn"
ID_LIKE="centos rhel fedora"
VERSION_ID="2"
PRETTY_NAME="Amazon Linux 2"
ANSI_COLOR="0;33"
CPE_NAME="cpe:2.3:o:amazon:amazon_linux:2"
HOME_URL="https://amazonlinux.com/"

Command Status: Success
```

## Development

1. Clone the repository:
```bash
git clone https://github.com/jyothishkshatri/ec2-restore.git
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

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 