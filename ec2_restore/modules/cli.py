import click
import yaml
import logging
import os
from typing import List, Optional, Dict
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table
from .aws_client import AWSClient
from .restore_manager import RestoreManager
from .display import display_volume_changes, display_instance_changes
from datetime import datetime

console = Console()
logger = logging.getLogger(__name__)

def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error loading config file: {str(e)}[/red]")
        raise

def setup_logging(config: dict):
    """Setup logging configuration."""
    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(config['restore']['log_file'])
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    # Setup file handler
    file_handler = logging.FileHandler(config['restore']['log_file'])
    file_handler.setLevel(getattr(logging, config['restore']['log_level']))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    
    # Setup root logger with only file handler
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config['restore']['log_level']))
    root_logger.addHandler(file_handler)
    
    # Disable propagation to avoid duplicate logs
    root_logger.propagate = False

def display_amis(amis: List[dict]):
    """Display available AMIs in a table format."""
    table = Table(title="Available AMIs")
    table.add_column("Index", style="cyan")
    table.add_column("AMI ID", style="green")
    table.add_column("Creation Date", style="yellow")
    table.add_column("Description", style="white")

    for idx, ami in enumerate(amis, 1):
        table.add_row(
            str(idx),
            ami['ImageId'],
            ami['CreationDate'],
            ami.get('Description', 'N/A')
        )

    console.print(table)
    console.print("\n[bold]Enter 'q' or 'quit' at any prompt to exit gracefully[/bold]")

def display_volumes(volumes: List[dict]):
    """Display available volumes in a table format."""
    table = Table(title="Available Volumes")
    table.add_column("Index", style="cyan", justify="right")
    table.add_column("Device", style="green")
    table.add_column("Size (GB)", style="yellow", justify="right")
    table.add_column("Type", style="blue")
    table.add_column("Delete on Termination", style="red", justify="center")

    for idx, volume in enumerate(volumes, 1):
        # Color the row based on delete on termination setting
        row_style = "red" if volume['DeleteOnTermination'] else "green"
        
        table.add_row(
            str(idx),
            volume['Device'],
            str(volume['Size']),
            volume['VolumeType'],
            str(volume['DeleteOnTermination']),
            style=row_style
        )

    console.print(table)
    console.print("\n[bold]Enter 'q' or 'quit' at any prompt to exit gracefully[/bold]")

def handle_quit_input(user_input: str) -> bool:
    """Check if user wants to quit."""
    return user_input.lower() in ['q', 'quit']

def display_progress(description: str, duration: float):
    """Display progress with duration."""
    console.print(f"[green]✓[/green] {description} ({duration:.2f} seconds)")

@click.group()
def cli():
    """EC2 Instance Restore Tool"""
    pass

@cli.command()
@click.option('--instance-id', help='EC2 instance ID to restore')
@click.option('--instance-name', help='EC2 instance name (tag) to restore')
@click.option('--instance-ids', help='Comma-separated list of EC2 instance IDs to restore')
@click.option('--config', default='config.yaml', help='Path to configuration file')
def restore(instance_id: Optional[str], instance_name: Optional[str],
            instance_ids: Optional[str], config: str):
    """Restore EC2 instance(s) from AMI"""
    start_time = datetime.now()
    try:
        # Load configuration
        config_data = load_config(config)
        setup_logging(config_data)
        logger.info("Starting EC2 instance restore process")

        # Initialize AWS client
        aws_client = AWSClient(
            profile_name=config_data['aws']['profile'],
            region=config_data['aws']['region']
        )
        restore_manager = RestoreManager(aws_client)

        # Get instance IDs
        target_instances = []
        if instance_ids:
            target_instances = instance_ids.split(',')
        elif instance_id:
            target_instances = [instance_id]
        elif instance_name:
            instance = aws_client.get_instance_by_name(instance_name)
            target_instances = [instance['InstanceId']]

        if not target_instances:
            console.print("[red]No instances specified for restoration[/red]")
            return

        logger.info(f"Processing {len(target_instances)} instances: {', '.join(target_instances)}")

        # Process each instance
        for instance_id in target_instances:
            instance_start = datetime.now()
            try:
                # Get instance details and backup metadata
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console
                ) as progress:
                    progress.add_task(description=f"Getting details for instance {instance_id}...")
                    instance = aws_client.get_instance_by_id(instance_id)
                    
                    # Backup instance metadata
                    progress.add_task(description="Backing up instance metadata...")
                    backup_file = restore_manager.backup_instance_metadata(instance_id)
                    console.print(f"[green]Instance metadata backed up to: {backup_file}[/green]")

                    # Get available AMIs
                    progress.add_task(description="Fetching available AMIs...")
                    amis = aws_client.get_instance_amis(
                        instance_id,
                        config_data['restore']['max_amis']
                    )

                if not amis:
                    console.print(f"[red]No AMIs found for instance {instance_id}[/red]")
                    continue

                # Display AMIs and get user selection
                display_amis(amis)
                ami_selection = Prompt.ask(
                    "Select AMI to restore from",
                    choices=[str(i) for i in range(1, len(amis) + 1)] + ['q', 'quit']
                )
                
                if handle_quit_input(ami_selection):
                    console.print("[yellow]Operation cancelled by user[/yellow]")
                    return
                
                ami_index = int(ami_selection) - 1
                selected_ami = amis[ami_index]
                logger.info(f"Selected AMI: {selected_ami['ImageId']}")

                # Get restore type
                restore_type = Prompt.ask(
                    "Select restore type",
                    choices=["full", "volume", "q", "quit"],
                    default="full"
                )
                
                if handle_quit_input(restore_type):
                    console.print("[yellow]Operation cancelled by user[/yellow]")
                    return

                logger.info(f"Selected restore type: {restore_type}")

                if restore_type == "full":
                    # Display instance changes before proceeding
                    console.print("\n[bold]Instance Changes Before Restore:[/bold]")
                    display_instance_changes(instance, selected_ami)
                    
                    # Full instance restore
                    if Confirm.ask("This will create a new instance. Continue?"):
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            console=console
                        ) as progress:
                            progress.add_task(description="Performing full instance restore...")
                            new_instance_id = restore_manager.full_instance_restore(
                                instance_id,
                                selected_ami['ImageId']
                            )
                        console.print(f"[green]New instance created with ID: {new_instance_id}[/green]")
                        
                        # Get new instance details and AMI volumes for report generation
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            console=console
                        ) as progress:
                            progress.add_task(description="Getting new instance details...")
                            new_instance = aws_client.get_instance_by_id(new_instance_id)
                            
                            # Wait for the new instance to be fully available
                            progress.add_task(description="Waiting for instance to be fully available...")
                            aws_client.wait_for_instance_availability(new_instance_id)
                            
                            # Get network interface details for both instances
                            old_network_interface = None
                            new_network_interface = None
                            for interface in instance.get('NetworkInterfaces', []):
                                if interface['Attachment']['DeviceIndex'] == 0:
                                    old_network_interface = interface
                                    break
                            
                            for interface in new_instance.get('NetworkInterfaces', []):
                                if interface['Attachment']['DeviceIndex'] == 0:
                                    new_network_interface = interface
                                    break
                            
                            # Verify private IP preservation
                            if old_network_interface and new_network_interface:
                                old_ip = old_network_interface['PrivateIpAddress']
                                new_ip = new_network_interface['PrivateIpAddress']
                                if old_ip != new_ip:
                                    console.print(f"[yellow]Warning: Private IP changed from {old_ip} to {new_ip}[/yellow]")
                            
                            progress.add_task(description="Getting AMI volumes for report...")
                            ami_volumes = aws_client.get_instance_volumes(selected_ami['ImageId'], is_ami=True)
                        
                        # Display final instance changes
                        console.print("\n[bold]Instance Changes After Restore:[/bold]")
                        display_instance_changes(instance, selected_ami, new_instance_id)
                        
                        # Display volume changes for the new instance
                        console.print("\n[bold]New Instance Volume Configuration:[/bold]")
                        new_instance_volumes = aws_client.get_instance_volumes(new_instance_id, is_ami=False)
                        display_volumes(new_instance_volumes)
                else:
                    # Volume restore
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console
                    ) as progress:
                        progress.add_task(description="Fetching available volumes...")
                        volumes = aws_client.get_instance_volumes(selected_ami['ImageId'], is_ami=True)
                    display_volumes(volumes)

                    # Get volume selection
                    volume_selection = Prompt.ask(
                        "Select volumes to restore (comma-separated indices or 'all')",
                        default="all"
                    )
                    
                    if handle_quit_input(volume_selection):
                        console.print("[yellow]Operation cancelled by user[/yellow]")
                        return
                    
                    if volume_selection.lower() == 'all':
                        selected_volumes = [v['Device'] for v in volumes]
                    else:
                        try:
                            # Split the input and clean up each index
                            indices = [int(i.strip()) for i in volume_selection.split(',')]
                            # Validate indices
                            if not all(1 <= idx <= len(volumes) for idx in indices):
                                raise ValueError("Invalid index provided")
                            # Convert to 0-based indices
                            indices = [idx - 1 for idx in indices]
                            selected_volumes = [volumes[i]['Device'] for i in indices]
                        except (ValueError, IndexError) as e:
                            console.print(f"[red]Invalid selection: {str(e)}[/red]")
                            console.print("[yellow]Please provide valid comma-separated numbers or 'all'[/yellow]")
                            return

                    logger.info(f"Selected volumes for restore: {', '.join(selected_volumes)}")

                    # Get current volumes for comparison
                    current_volumes = aws_client.get_instance_volumes(instance_id, is_ami=False)
                    console.print("\n[bold]Current Volume Configuration:[/bold]")
                    display_volumes(current_volumes)

                    if Confirm.ask("This will modify the existing instance. Continue?"):
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            console=console
                        ) as progress:
                            progress.add_task(description="Performing volume restore...")
                            restore_manager.volume_restore(
                                instance_id,
                                selected_ami['ImageId'],
                                selected_volumes
                            )
                        console.print("[green]Volume restore completed successfully[/green]")
                        
                        # Get updated volumes with new volume IDs
                        updated_volumes = aws_client.get_instance_volumes(instance_id, is_ami=False)
                        ami_volumes = aws_client.get_instance_volumes(selected_ami['ImageId'], is_ami=True)
                        
                        # Update AMI volumes with new volume IDs
                        for volume in updated_volumes:
                            for ami_volume in ami_volumes:
                                if volume['Device'] == ami_volume['Device']:
                                    ami_volume['NewVolumeId'] = volume['VolumeId']
                                    break
                        
                        # Display volume changes
                        console.print("\n[bold]Volume Changes:[/bold]")
                        display_volume_changes(current_volumes, ami_volumes, selected_volumes, aws_client)

                # Generate report
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console
                ) as progress:
                    progress.add_task(description="Generating restoration report...")
                    report_file = restore_manager.generate_restore_report(
                        instance_id=instance_id,
                        restore_type=restore_type,
                        ami_id=selected_ami['ImageId'],
                        new_instance_id=new_instance_id if restore_type == "full" else None,
                        current_volumes=current_volumes if restore_type == 'volume' else None,
                        ami_volumes=ami_volumes,
                        backup_file=backup_file
                    )
                console.print(f"[green]Restoration report generated: {report_file}[/green]")

                instance_duration = datetime.now() - instance_start
                display_progress(f"Instance {instance_id} processed successfully", instance_duration.total_seconds())

            except Exception as e:
                instance_duration = datetime.now() - instance_start
                logger.error(f"Error processing instance {instance_id} after {instance_duration.total_seconds():.2f} seconds: {str(e)}")
                console.print(f"[red]Error processing instance {instance_id}: {str(e)}[/red]")
                if Confirm.ask("Continue with next instance?"):
                    continue
                else:
                    break

        total_duration = datetime.now() - start_time
        logger.info(f"EC2 instance restore process completed in {total_duration.total_seconds():.2f} seconds")
        display_progress("EC2 instance restore process completed", total_duration.total_seconds())

    except Exception as e:
        total_duration = datetime.now() - start_time
        logger.error(f"Error during restoration after {total_duration.total_seconds():.2f} seconds: {str(e)}")
        console.print(f"[red]Error during restoration: {str(e)}[/red]")
        raise click.Abort()

if __name__ == '__main__':
    cli()