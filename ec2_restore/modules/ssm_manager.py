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
        """
        Run configured Systems Manager commands on the instance.
        
        Args:
            instance_id: The ID of the instance to run commands on
            
        Returns:
            bool: True if all commands executed successfully, False otherwise
        """
        if not self.ssm_enabled:
            console.print("[yellow]Systems Manager is not enabled. Skipping command execution.[/yellow]")
            return True

        if not self.commands:
            console.print("[yellow]No commands configured. Skipping command execution.[/yellow]")
            return True

        console.print("\n[bold]Executing Systems Manager Commands[/bold]")
        self.display_commands()

        if not Confirm.ask("Do you want to proceed with executing these commands?"):
            console.print("[yellow]Command execution cancelled by user.[/yellow]")
            return False

        success = True
        for cmd in self.commands:
            try:
                console.print(f"\n[bold]Executing: {cmd['name']}[/bold]")
                console.print(f"Command: {cmd['command']}")

                # Prepare command parameters
                parameters = {
                    'commands': [cmd['command']],
                    'executionTimeout': [str(cmd['timeout'])]
                }

                # Add S3 output configuration if specified
                if self.output_s3_bucket:
                    parameters['outputS3BucketName'] = [self.output_s3_bucket]
                    if self.output_s3_prefix:
                        parameters['outputS3KeyPrefix'] = [self.output_s3_prefix]

                # Send command
                response = self.aws_client.ssm_client.send_command(
                    InstanceIds=[instance_id],
                    DocumentName=self.document_name,
                    Parameters=parameters,
                    TimeoutSeconds=cmd['timeout']
                )

                command_id = response['Command']['CommandId']
                console.print(f"Command ID: {command_id}")

                if cmd.get('wait_for_completion', True):
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console
                    ) as progress:
                        task = progress.add_task(f"Waiting for command completion...", total=None)
                        
                        while True:
                            result = self.aws_client.ssm_client.get_command_invocation(
                                CommandId=command_id,
                                InstanceId=instance_id
                            )
                            
                            if result['Status'] in ['Success', 'Failed', 'Cancelled', 'TimedOut']:
                                break
                            
                            time.sleep(5)

                    # Display command output
                    if result['Status'] == 'Success':
                        console.print(f"[green]✓ Command completed successfully[/green]")
                        if 'StandardOutputContent' in result:
                            console.print("\nCommand Output:")
                            console.print(result['StandardOutputContent'])
                    else:
                        console.print(f"[red]✗ Command failed with status: {result['Status']}[/red]")
                        if 'StandardErrorContent' in result:
                            console.print("\nError Output:")
                            console.print(result['StandardErrorContent'])
                        success = False
                        break

            except Exception as e:
                logger.error(f"Error executing command: {str(e)}")
                console.print(f"[red]Error executing command: {str(e)}[/red]")
                success = False
                break

        return success 