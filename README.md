# EC2 Restore Tool

A powerful tool for restoring EC2 instances from AMIs with support for Systems Manager command execution.

## Features

- Full instance restore from AMI
- Volume-level restore
- Systems Manager command execution after restore
- Detailed restoration reports
- Beautiful CLI interface with progress tracking

## Installation

```bash
pip install ec2-restore
```

## Configuration

Create a `config.yaml` file with the following structure:

```yaml
aws:
  profile: default  # AWS profile to use
  region: us-east-1  # Default region

restore:
  max_amis: 5  # Number of AMIs to show in selection
  backup_metadata: true  # Whether to backup instance metadata
  log_level: INFO  # Logging level
  log_file: ec2_restore.log  # Log file path

systems_manager:
  enabled: false  # Whether to run Systems Manager commands after restore
  commands:  # List of commands to run after instance restore
    - name: "Update System Packages"  # Friendly name for the command
      command: "yum update -y"  # The actual command to run
      timeout: 300  # Command timeout in seconds
      wait_for_completion: true  # Whether to wait for command completion
    - name: "Install Required Packages"
      command: "yum install -y aws-cli"
      timeout: 300
      wait_for_completion: true
  document_name: "AWS-RunShellScript"  # Default SSM document to use
  output_s3_bucket: ""  # Optional S3 bucket for command output
  output_s3_prefix: ""  # Optional S3 prefix for command output
```

### Systems Manager Configuration

The Systems Manager section allows you to configure commands that will be executed after a successful instance restore:

- `enabled`: Set to `true` to enable Systems Manager command execution
- `commands`: List of commands to execute, each with:
  - `name`: Friendly name for the command
  - `command`: The actual command to run
  - `timeout`: Command timeout in seconds
  - `wait_for_completion`: Whether to wait for command completion
- `document_name`: The SSM document to use (default: "AWS-RunShellScript")
- `output_s3_bucket`: Optional S3 bucket for command output
- `output_s3_prefix`: Optional S3 prefix for command output

## Usage

### Full Instance Restore

```bash
ec2-restore restore --instance-id i-1234567890abcdef0
```

The tool will:
1. Display available AMIs
2. Show instance changes before restore
3. Create a new instance
4. Execute Systems Manager commands (if enabled)
5. Display final instance changes
6. Generate a restore report

### Volume Restore

```bash
ec2-restore restore --instance-id i-1234567890abcdef0 --type volume
```

### Restore by Instance Name

```bash
ec2-restore restore --instance-name my-instance
```

### Restore Multiple Instances

```bash
ec2-restore restore --instance-ids i-1234567890abcdef0,i-0987654321fedcba0
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

3. Install dependencies:
```bash
pip install -e .
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 