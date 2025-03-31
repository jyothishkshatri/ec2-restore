from typing import List, Dict
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from .aws_client import AWSClient
from rich import box
from rich.live import Live
from rich.spinner import Spinner
import time

console = Console()

def display_volume_changes(current_volumes: List[Dict], ami_volumes: List[Dict], selected_devices: List[str], aws_client) -> None:
    """Display volume changes in a table format."""
    # Create a mapping of device to volume information
    volume_map = {}
    for volume in current_volumes:
        volume_map[volume['Device']] = {
            'previous_id': volume['VolumeId'],
            'new_id': None,
            'status': 'Pending',
            'type': volume.get('VolumeType', 'gp3'),
            'size': volume.get('Size', 'N/A')
        }
    
    # Update volume information from AMI volumes
    for volume in ami_volumes:
        if volume['Device'] in volume_map:
            volume_map[volume['Device']]['snapshot_id'] = volume['VolumeId']
            if 'NewVolumeId' in volume:
                volume_map[volume['Device']]['new_id'] = volume['NewVolumeId']
                volume_map[volume['Device']]['status'] = 'Available'
    
    # Create table with enhanced styling
    table = Table(
        title="Volume Changes",
        title_style="bold magenta",
        border_style="bright_blue",
        show_header=True,
        header_style="bold cyan"
    )
    
    # Add columns with distinct colors and alignment
    table.add_column("Device", style="cyan", justify="left", no_wrap=True)
    table.add_column("Previous Volume ID", style="red", justify="left", no_wrap=True)
    table.add_column("Snapshot ID", style="yellow", justify="left", no_wrap=True)
    table.add_column("New Volume ID", style="green", justify="left", no_wrap=True)
    table.add_column("Status", style="blue", justify="center")
    table.add_column("Type", style="magenta", justify="center")
    table.add_column("Size (GB)", style="white", justify="right")
    
    # Add rows with status-based coloring
    for device in selected_devices:
        if device in volume_map:
            vol_info = volume_map[device]
            # Color the row based on status
            row_style = "green" if vol_info['status'] == 'Available' else "yellow" if vol_info['status'] == 'Pending' else "red"
            
            table.add_row(
                device,
                vol_info['previous_id'],
                vol_info.get('snapshot_id', 'N/A'),
                vol_info['new_id'] or 'N/A',
                vol_info['status'],
                vol_info['type'],
                str(vol_info['size']),
                style=row_style
            )
    
    console.print(table)
    console.print("\n[bold]Enter 'q' or 'quit' at any prompt to exit gracefully[/bold]")
    
    # Add warning about attachment points if needed
    for device in selected_devices:
        if device in volume_map:
            try:
                volume = aws_client.get_volume(volume_map[device]['previous_id'])
                if volume['State'] == 'in-use':
                    console.print(Panel(
                        Text(f"Warning: Device {device} is currently in use. The system will attempt to detach the existing volume before attaching the new one.", style="yellow"),
                        title="Attachment Point Warning"
                    ))
            except Exception:
                pass

def display_instance_changes(instance: Dict, ami: Dict, new_instance_id: str = None) -> None:
    """Display instance changes in a table format during full restore."""
    # Create table with enhanced styling
    table = Table(
        title="Instance Changes",
        title_style="bold magenta",
        border_style="bright_blue",
        show_header=True,
        header_style="bold cyan"
    )
    
    # Add columns with distinct colors and alignment
    table.add_column("Property", style="cyan", justify="left", no_wrap=True)
    table.add_column("Previous Value", style="red", justify="left", no_wrap=True)
    table.add_column("New Value", style="green", justify="left", no_wrap=True)
    table.add_column("Status", style="blue", justify="center")
    
    # Get instance name from tags
    instance_name = None
    for tag in instance.get('Tags', []):
        if tag['Key'] == 'Name':
            instance_name = tag['Value']
            break
    
    # Get network interface details
    network_interface = None
    private_ip = None
    for interface in instance.get('NetworkInterfaces', []):
        if interface['Attachment']['DeviceIndex'] == 0:  # Primary network interface
            network_interface = interface
            private_ip = interface['PrivateIpAddress']
            break
    
    # Get security groups
    security_groups = [sg['GroupId'] for sg in instance.get('SecurityGroups', [])]
    
    # Add rows with instance information
    rows = [
        ("Instance ID", instance['InstanceId'], new_instance_id or "N/A", "Pending" if not new_instance_id else "Available"),
        ("Instance Name", instance_name or "N/A", instance_name or "N/A", "Preserved"),
        ("Instance Type", instance['InstanceType'], instance['InstanceType'], "Preserved"),
        ("Availability Zone", instance['Placement']['AvailabilityZone'], instance['Placement']['AvailabilityZone'], "Preserved"),
        ("AMI ID", "N/A", ami['ImageId'], "Selected"),
        ("State", instance['State']['Name'], "Pending" if not new_instance_id else "Running", "Pending" if not new_instance_id else "Available")
    ]
    
    # Add network information
    if network_interface:
        rows.extend([
            ("Network Interface ID", network_interface['NetworkInterfaceId'], network_interface['NetworkInterfaceId'], "Preserved"),
            ("Private IP", private_ip, private_ip, "Preserved" if new_instance_id else "Pending"),
            ("Subnet ID", network_interface['SubnetId'], network_interface['SubnetId'], "Preserved"),
            ("VPC ID", network_interface['VpcId'], network_interface['VpcId'], "Preserved")
        ])
    
    # Add security groups
    if security_groups:
        rows.append(("Security Groups", ", ".join(security_groups), ", ".join(security_groups), "Preserved"))
    
    # Add IAM role information if present
    if 'IamInstanceProfile' in instance:
        iam_profile = instance['IamInstanceProfile']
        profile_name = iam_profile.get('Name', iam_profile.get('Arn', '').split('/')[-1])
        rows.append(("IAM Role", profile_name, profile_name, "Preserved"))
    
    # Add key pair information if present
    if 'KeyName' in instance:
        rows.append(("Key Pair", instance['KeyName'], instance['KeyName'], "Preserved"))
    
    # Add user data information if present
    if 'UserData' in instance:
        rows.append(("User Data", "Present", "Present", "Preserved"))
    
    # Add rows to table with status-based coloring
    for property_name, prev_value, new_value, status in rows:
        # Color the row based on status
        row_style = "green" if status == "Available" else "yellow" if status == "Pending" else "red" if status == "Error" else "white"
        
        table.add_row(
            property_name,
            prev_value,
            new_value,
            status,
            style=row_style
        )
    
    console.print(table)
    
    # Add a note about private IP preservation
    if network_interface and private_ip:
        console.print("\n[bold yellow]Note:[/bold yellow] The private IP address will be preserved during the restore process.")
    
    console.print("\n[bold]Enter 'q' or 'quit' at any prompt to exit gracefully[/bold]")