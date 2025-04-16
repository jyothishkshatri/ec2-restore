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
from rich.prompt import Confirm, Prompt

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
                    # Add a small delay to ensure command is registered
                    time.sleep(5)
                    
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
            
    def run_interactive_session(self, instance_id: str) -> None:
        """Run an interactive SSM command session on the instance.
        
        This allows the user to execute individual commands on the instance.
        """
        console.print(f"\n[bold]Starting interactive SSM session for instance: {instance_id}[/bold]")
        
        # Check if we have predefined commands
        if self.commands:
            console.print("[cyan]You have the following predefined commands available:[/cyan]")
            self.display_commands()
            
            use_predefined = Confirm.ask("Do you want to run one of these predefined commands?")
            if use_predefined:
                command_names = [cmd['name'] for cmd in self.commands]
                selected = Prompt.ask(
                    "Select a command to run",
                    choices=command_names + ['all', 'quit'],
                    default=command_names[0] if command_names else 'quit'
                )
                
                if selected == 'quit':
                    return
                
                if selected == 'all':
                    self.run_commands(instance_id)
                    return
                
                # Find the selected command
                for cmd in self.commands:
                    if cmd['name'] == selected:
                        self._run_single_command(
                            instance_id,
                            cmd['command'],
                            cmd['timeout'],
                            cmd.get('wait_for_completion', True)
                        )
                        break
                return
        
        # Custom command mode
        while True:
            console.print("\n[cyan]Enter a command to run on the instance (type 'exit' to quit):[/cyan]")
            command = Prompt.ask("Command")
            
            if command.lower() in ['exit', 'quit']:
                break
                
            timeout = Prompt.ask("Command timeout (seconds)", default="300")
            wait = Confirm.ask("Wait for command completion?", default=True)
            
            self._run_single_command(instance_id, command, int(timeout), wait)
            
            if not Confirm.ask("Run another command?", default=True):
                break
                
    def _run_single_command(self, instance_id: str, command: str, timeout: int, wait_for_completion: bool) -> bool:
        """Run a single command on the instance."""
        try:
            console.print(f"\n[bold cyan]Executing command on {instance_id}[/bold cyan]")
            console.print(f"[green]Command: {command}[/green]")
            
            # Send command
            command_id = self.aws_client.send_command(
                instance_id,
                command,
                self.document_name,
                timeout,
                self.output_s3_bucket,
                self.output_s3_prefix
            )
            
            if wait_for_completion:
                # Add a small delay to ensure command is registered
                time.sleep(5)
                
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
                
                console.print(f"\n[bold]Command Status: {status}[/bold]")
                return status == 'Success'
            else:
                console.print("[yellow]Command sent (not waiting for completion)[/yellow]")
                return True
                
        except Exception as e:
            logger.error(f"Error executing Systems Manager command: {str(e)}")
            console.print(f"[red]Error executing Systems Manager command: {str(e)}[/red]")
            return False 