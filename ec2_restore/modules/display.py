from typing import List, Dict
from rich.console import Console
from rich.table import Table
from .aws_client import AWSClient

console = Console()

def display_volume_changes(current_volumes: List[Dict], ami_volumes: List[Dict], selected_devices: List[str], aws_client: AWSClient) -> None:
    """Display volume changes in a table format."""
    table = Table(title="Volume Changes")
    table.add_column("Device", style="cyan")
    table.add_column("Previous Volume ID", style="yellow")
    table.add_column("Snapshot ID", style="magenta")
    table.add_column("New Volume ID", style="green")
    table.add_column("Status", style="bold")

    # Create a mapping of device to volume ID for current volumes
    current_volume_map = {v['Device']: v['VolumeId'] for v in current_volumes}
    
    # Create a mapping of device to volume info for AMI volumes
    ami_volume_map = {v['Device']: v for v in ami_volumes}

    for device in selected_devices:
        current_vol_id = current_volume_map.get(device, "N/A")
        ami_volume = ami_volume_map.get(device)
        
        if ami_volume:
            snapshot_id = ami_volume['VolumeId']  # This is the snapshot ID from AMI
            new_vol_id = ami_volume.get('NewVolumeId', 'N/A')  # This will be set during volume creation
            
            # Determine status based on volume state
            if new_vol_id != 'N/A':
                try:
                    # Get volume details to check attachment status
                    response = aws_client.ec2_client.describe_volumes(VolumeIds=[new_vol_id])
                    volume = response['Volumes'][0]
                    
                    # Check if volume is attached to the instance
                    if volume['State'] == 'in-use' and volume['Attachments']:
                        attachment = volume['Attachments'][0]
                        if attachment['State'] == 'attached':
                            status = "✓ Attached"
                        else:
                            status = f"⚠️ {attachment['State']}"
                    elif volume['State'] == 'available':
                        status = "⚠️ Available"
                    else:
                        status = f"⚠️ {volume['State']}"
                except Exception as e:
                    status = "⚠️ Error"
            else:
                status = "Pending"
        else:
            snapshot_id = "N/A"
            new_vol_id = "N/A"
            status = "✗ Not Found"

        table.add_row(
            device,
            current_vol_id,
            snapshot_id,
            new_vol_id,
            status
        )

    console.print(table) 