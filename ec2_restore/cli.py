from typing import List
import click

class CLI:
    def restore_instance(self, instance_id: str, ami_id: str, restore_type: str, volume_devices: List[str] = None):
        """Restore an instance from an AMI."""
        try:
            # Backup instance metadata
            backup_file = self.restore_manager.backup_instance_metadata(instance_id)
            self.console.print(f"[green]Instance metadata backed up to {backup_file}[/green]")

            # Perform restore based on type
            if restore_type == 'full':
                new_instance_id = self.restore_manager.full_instance_restore(instance_id, ami_id)
                self.console.print(f"[green]New instance created with ID: {new_instance_id}[/green]")
            else:  # volume restore
                new_instance_id = self.restore_manager.volume_restore(instance_id, ami_id, volume_devices)
                self.console.print(f"[green]Volume restoration completed for instance {new_instance_id}[/green]")

            # Generate restore report
            report_file = self.restore_manager.generate_restore_report(
                instance_id, backup_file, restore_type, new_instance_id
            )
            self.console.print(f"[green]Restore report generated: {report_file}[/green]")

            return True
        except Exception as e:
            self.console.print(f"[red]Error processing instance {instance_id}: {str(e)}[/red]")
            return False

    def restore(self, instance_id: str, ami_id: str, restore_type: str, volume_devices: List[str] = None):
        """Restore one or more instances from AMIs."""
        try:
            # Handle single instance restore
            if instance_id:
                with self.console.status("[bold blue]Performing restore...") as status:
                    success = self.restore_instance(instance_id, ami_id, restore_type, volume_devices)
                if not success:
                    self.console.print("[red]Restore operation failed[/red]")
                return

            # Handle multiple instances restore
            instances = self.aws_client.get_instances()
            if not instances:
                self.console.print("[yellow]No instances found[/yellow]")
                return

            self.console.print("\n[bold]Available Instances:[/bold]")
            for idx, instance in enumerate(instances, 1):
                name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), 'N/A')
                self.console.print(f"{idx}. {instance['InstanceId']} ({name})")

            while True:
                selection = self.console.input("\nSelect instances to restore (comma-separated indices or 'all') [all/q/quit/1/2/...]: ").strip()
                
                if selection.lower() in ['q', 'quit']:
                    self.console.print("[yellow]Operation cancelled by user[/yellow]")
                    return
                
                if selection.lower() == 'all':
                    selected_indices = list(range(1, len(instances) + 1))
                else:
                    try:
                        selected_indices = [int(idx.strip()) for idx in selection.split(',')]
                    except ValueError:
                        self.console.print("[red]Invalid selection. Please enter valid numbers or 'all'[/red]")
                        continue

                if not all(1 <= idx <= len(instances) for idx in selected_indices):
                    self.console.print("[red]Invalid index. Please select numbers within the available range[/red]")
                    continue

                selected_instances = [instances[idx-1] for idx in selected_indices]
                break

            # Confirm restore for selected instances
            self.console.print("\n[bold]Selected Instances:[/bold]")
            for instance in selected_instances:
                name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), 'N/A')
                self.console.print(f"- {instance['InstanceId']} ({name})")

            if not self.console.input("\nThis will modify the existing instances. Continue? [y/n]: ").lower().startswith('y'):
                self.console.print("[yellow]Operation cancelled by user[/yellow]")
                return

            # Process each selected instance
            for instance in selected_instances:
                with self.console.status(f"[bold blue]Processing instance {instance['InstanceId']}...") as status:
                    success = self.restore_instance(instance['InstanceId'], ami_id, restore_type, volume_devices)
                    if not success:
                        if len(selected_instances) > 1:
                            if not self.console.input("\nContinue with next instance? [y/n]: ").lower().startswith('y'):
                                self.console.print("[yellow]Operation cancelled by user[/yellow]")
                                return
                        else:
                            self.console.print("[red]Restore operation failed[/red]")
                            return

            self.console.print("[green]All selected instances processed successfully[/green]")

        except Exception as e:
            self.console.print(f"[red]Error during restore operation: {str(e)}[/red]")
            raise click.Abort() 