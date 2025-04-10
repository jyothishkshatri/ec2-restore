"""
Systems Manager (SSM) Manager Module

This module handles all Systems Manager operations for the EC2 Restore Tool.
"""
import time
import logging
from typing import List, Dict, Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.prompt import Confirm

logger = logging.getLogger(__name__)
console = Console()

class SSMManager:
    def __init__(self, aws_client, config: Dict):
        """Initialize the SSM Manager with AWS client and configuration."""
        self.aws_client = aws_client
        self.config = config
        self.ssm_enabled = config.get('systems_manager', {}).get('enabled', False)
        self.commands = config.get('systems_manager', {}).get('commands', [])
        self.document_name = config.get('systems_manager', {}).get('document_name', 'AWS-RunShellScript')
        self.output_s3_bucket = config.get('systems_manager', {}).get('output_s3_bucket', '')
        self.output_s3_prefix = config.get('systems_manager', {}).get('output_s3_prefix', '')

    def is_enabled(self) -> bool:
        """Check if Systems Manager is enabled in the configuration."""
        return self.ssm_enabled

    def display_commands(self) -> None:
        """Display the list of commands that will be executed."""
        if not self.commands:
            console.print("[yellow]No Systems Manager commands configured.[/yellow]")
            return

        table = Table(title="Systems Manager Commands")
        table.add_column("Name", style="cyan")
        table.add_column("Command", style="green")
        table.add_column("Timeout", style="yellow")
        table.add_column("Wait", style="blue")

        for cmd in self.commands:
            table.add_row(
                cmd['name'],
                cmd['command'],
                f"{cmd['timeout']}s",
                "Yes" if cmd.get('wait_for_completion', True) else "No"
            )

        console.print(table)

    def run_commands(self, instance_id: str) -> bool:
        """Run Systems Manager commands on the instance."""
        if not self.ssm_enabled or not self.commands:
            return True

        try:
            for cmd in self.commands:
                console.print(f"\n[bold cyan]Executing command: {cmd['name']}[/bold cyan]")
                console.print(f"[green]Command: {cmd['command']}[/green]")

                # Send command
                command_id = self.aws_client.send_command(
                    instance_id,
                    cmd['command'],
                    self.document_name,
                    cmd['timeout'],
                    self.output_s3_bucket,
                    self.output_s3_prefix
                )

                if cmd.get('wait_for_completion', True):
                    # Wait for command completion and show output
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console
                    ) as progress:
                        task = progress.add_task(description="Waiting for command completion...", total=None)
                        
                        while True:
                            status, output = self.aws_client.get_command_status(command_id, instance_id)
                            
                            if status in ['Success', 'Failed', 'Cancelled', 'TimedOut']:
                                progress.update(task, description=f"Command completed with status: {status}")
                                break
                            
                            time.sleep(5)  # Check status every 5 seconds

                    # Display command output
                    if output:
                        console.print("\n[bold]Command Output:[/bold]")
                        console.print(output)
                    
                    if status != 'Success':
                        console.print(f"[yellow]Command completed with status: {status}[/yellow]")
                        if not Confirm.ask("Continue with next command?"):
                            return False
                else:
                    console.print("[yellow]Command sent (not waiting for completion)[/yellow]")

            return True

        except Exception as e:
            logger.error(f"Error executing Systems Manager commands: {str(e)}")
            console.print(f"[red]Error executing Systems Manager commands: {str(e)}[/red]")
            return False 