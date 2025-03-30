import logging
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from .aws_client import AWSClient
import time
from .display import display_volume_changes

logger = logging.getLogger(__name__)

class RestoreManager:
    def __init__(self, aws_client: AWSClient, backup_dir: str = "backups"):
        """Initialize the restore manager."""
        self.aws_client = aws_client
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(exist_ok=True)

    def backup_instance_metadata(self, instance_id: str) -> str:
        """Backup instance metadata before restoration."""
        try:
            instance = self.aws_client.get_instance_by_id(instance_id)
            
            # Extract instance name from tags
            instance_name = None
            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Name':
                    instance_name = tag['Value']
                    break
            
            # Add instance name to metadata
            metadata = {
                'InstanceId': instance_id,
                'InstanceName': instance_name,
                'InstanceDetails': instance
            }
            
            backup_file = self.backup_dir / f"instance_{instance_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(backup_file, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            
            return str(backup_file)
        except Exception as e:
            logger.error(f"Error backing up instance metadata: {str(e)}")
            raise

    def get_instance_network_config(self, instance: Dict) -> List[Dict]:
        """Extract network interface configuration from instance."""
        network_interfaces = []
        for interface in instance.get('NetworkInterfaces', []):
            network_interface = {
                'NetworkInterfaceId': interface['NetworkInterfaceId'],
                'DeviceIndex': interface['Attachment']['DeviceIndex'],
                'SubnetId': interface['SubnetId'],
                'Groups': [group['GroupId'] for group in interface['Groups']],
                'PrivateIpAddress': interface['PrivateIpAddress']
            }
            network_interfaces.append(network_interface)
        return network_interfaces

    def full_instance_restore(self, instance_id: str, ami_id: str) -> str:
        """Perform a full instance restoration."""
        try:
            # Backup instance metadata
            backup_file = self.backup_instance_metadata(instance_id)
            logger.info(f"Instance metadata backed up to {backup_file}")

            # Get instance details
            instance = self.aws_client.get_instance_by_id(instance_id)
            
            # Get network interface ID and modify its DeleteOnTermination attribute
            network_interface_id = None
            attachment_id = None
            for interface in instance.get('NetworkInterfaces', []):
                if interface['Attachment']['DeviceIndex'] == 0:  # Primary network interface
                    network_interface_id = interface['NetworkInterfaceId']
                    attachment_id = interface['Attachment']['AttachmentId']
                    # Modify the network interface to persist after instance termination
                    logger.info(f"Modifying network interface {network_interface_id} to persist after termination")
                    self.aws_client.modify_network_interface_attribute(
                        network_interface_id=network_interface_id,
                        attachment_id=attachment_id,
                        delete_on_termination=False
                    )
                    break

            if not network_interface_id:
                raise ValueError("No primary network interface found")

            # Store old volume IDs for cleanup
            old_volumes = []
            for block_device in instance.get('BlockDeviceMappings', []):
                if 'Ebs' in block_device:
                    old_volumes.append(block_device['Ebs']['VolumeId'])

            # Stop and terminate the existing instance
            logger.info("Stopping existing instance...")
            self.aws_client.stop_instance(instance_id)
            self.aws_client.wait_for_instance_state(instance_id, 'stopped')
            
            logger.info("Terminating existing instance...")
            self.aws_client.terminate_instance(instance_id)
            
            # Wait for the instance to be terminated and ENI to be available
            logger.info("Waiting for instance termination and ENI availability...")
            self.aws_client.wait_for_instance_state(instance_id, 'terminated')
            time.sleep(30)  # Additional wait time for ENI to be fully available
            
            # Prepare instance configuration
            launch_params = {
                'ImageId': ami_id,
                'InstanceType': instance['InstanceType'],
                'NetworkInterfaces': [{
                    'NetworkInterfaceId': network_interface_id,
                    'DeviceIndex': 0
                }],
                'MinCount': 1,
                'MaxCount': 1
            }

            # Add IAM role if present
            if 'IamInstanceProfile' in instance:
                iam_profile = instance['IamInstanceProfile']
                if 'Arn' in iam_profile:
                    # Extract the profile name from the ARN
                    profile_name = iam_profile['Arn'].split('/')[-1]
                    launch_params['IamInstanceProfile'] = {
                        'Name': profile_name
                    }
                elif 'Name' in iam_profile:
                    launch_params['IamInstanceProfile'] = {
                        'Name': iam_profile['Name']
                    }

            # Add key pair if present
            if 'KeyName' in instance:
                launch_params['KeyName'] = instance['KeyName']

            # Add placement information
            if 'Placement' in instance:
                launch_params['Placement'] = instance['Placement']

            # Add user data if present
            if 'UserData' in instance:
                launch_params['UserData'] = instance['UserData']

            # Create new instance with same configuration
            new_instance_id = self.aws_client.create_instance_with_config(launch_params)
            logger.info(f"New instance created with ID: {new_instance_id}")

            # Wait for the new instance to be fully available in AWS
            logger.info("Waiting for new instance to be fully available in AWS...")
            self.aws_client.wait_for_instance_availability(new_instance_id, timeout=300)  # Wait up to 5 minutes

            # Restore tags
            if 'Tags' in instance:
                logger.info("Restoring instance tags...")
                self.aws_client.create_tags(
                    new_instance_id,
                    instance['Tags']
                )

            # Clean up old volumes
            if old_volumes:
                logger.info("Cleaning up old volumes...")
                for volume_id in old_volumes:
                    try:
                        logger.info(f"Deleting old volume {volume_id}")
                        self.aws_client.delete_volume(volume_id)
                    except Exception as e:
                        logger.error(f"Error deleting old volume {volume_id}: {str(e)}")
                        # Continue with other volumes even if one fails

            return new_instance_id

        except Exception as e:
            logger.error(f"Error during full instance restoration: {str(e)}")
            raise

    def volume_restore(self, instance_id: str, ami_id: str, volume_devices: List[str]) -> str:
        """Perform a volume-level restoration."""
        start_time = datetime.now()
        logger.info(f"Starting volume restore for instance {instance_id} from AMI {ami_id}")
        logger.info(f"Selected volume devices: {', '.join(volume_devices)}")
        
        snapshots = {}
        created_volumes = {}  # Track volumes created during the process
        original_state = None  # Track original instance state
        try:
            # Backup instance metadata
            metadata_start = datetime.now()
            backup_file = self.backup_instance_metadata(instance_id)
            metadata_duration = datetime.now() - metadata_start
            logger.info(f"Instance metadata backed up to {backup_file} in {metadata_duration.total_seconds():.2f} seconds")

            # Get instance details and check state
            instance = self.aws_client.get_instance_by_id(instance_id)
            original_state = instance['State']['Name']
            logger.info(f"Instance {instance_id} current state: {original_state}")
            
            if original_state not in ['running', 'stopped']:
                raise ValueError(f"Instance {instance_id} is in state {original_state}. Instance must be either running or stopped.")
            
            # Get volumes from AMI
            ami_start = datetime.now()
            ami_volumes = self.aws_client.get_instance_volumes(ami_id, is_ami=True)
            ami_duration = datetime.now() - ami_start
            logger.info(f"Retrieved {len(ami_volumes)} volumes from AMI {ami_id} in {ami_duration.total_seconds():.2f} seconds")
            
            if not ami_volumes:
                raise ValueError(f"No volumes found in AMI {ami_id}")
            
            # Create snapshots of current volumes
            current_volumes = self.aws_client.get_instance_volumes(instance_id, is_ami=False)
            logger.info(f"Found {len(current_volumes)} current volumes on instance {instance_id}")
            
            snapshot_start = datetime.now()
            for volume in current_volumes:
                logger.info(f"Creating snapshot for volume {volume['VolumeId']} ({volume['Device']})")
                snapshot_id = self.aws_client.create_volume_snapshot(
                    volume['VolumeId'],
                    f"Pre-restore backup of {volume['VolumeId']}"
                )
                snapshots[volume['VolumeId']] = snapshot_id
                logger.info(f"Created snapshot {snapshot_id} for volume {volume['VolumeId']}")
            snapshot_duration = datetime.now() - snapshot_start
            logger.info(f"Created {len(snapshots)} snapshots in {snapshot_duration.total_seconds():.2f} seconds")

            # Create new volumes from AMI
            volume_start = datetime.now()
            new_volumes = {}
            for volume in ami_volumes:
                if volume['Device'] in volume_devices:
                    logger.info(f"Creating new volume from AMI snapshot {volume['VolumeId']} for device {volume['Device']}")
                    new_volume_id = self.aws_client.create_volume_from_snapshot(
                        volume['VolumeId'],  # This is the snapshot ID from the AMI
                        instance['Placement']['AvailabilityZone'],
                        volume['VolumeType']
                    )
                    new_volumes[volume['Device']] = new_volume_id
                    created_volumes[new_volume_id] = volume['Device']  # Track created volume
                    volume['NewVolumeId'] = new_volume_id  # Store the new volume ID in the AMI volume info
                    logger.info(f"Created new volume {new_volume_id} from snapshot {volume['VolumeId']}")
                    
                    # Wait for the new volume to be available
                    logger.info(f"Waiting for new volume {new_volume_id} to be available...")
                    self.aws_client.wait_for_volume_available(new_volume_id)
                    logger.info(f"New volume {new_volume_id} is now available")
            volume_duration = datetime.now() - volume_start
            logger.info(f"Created {len(new_volumes)} new volumes in {volume_duration.total_seconds():.2f} seconds")

            # Display volume changes before proceeding with attachment
            display_volume_changes(current_volumes, ami_volumes, volume_devices, self.aws_client)

            # Stop instance if it's running
            if original_state == 'running':
                logger.info(f"Stopping instance {instance_id}")
                self.aws_client.stop_instance(instance_id)
                # Wait for instance to be stopped
                self.aws_client.wait_for_instance_state(instance_id, 'stopped')
                logger.info(f"Instance {instance_id} stopped successfully")

            # Detach old volumes and attach new ones
            attach_start = datetime.now()
            for volume in current_volumes:
                if volume['Device'] in volume_devices:
                    logger.info(f"Detaching volume {volume['VolumeId']} from device {volume['Device']}")
                    try:
                        self.aws_client.detach_volume(volume['VolumeId'])
                        # Wait for volume to be detached
                        self.aws_client.wait_for_volume_detachment(volume['VolumeId'])
                        logger.info(f"Detached volume {volume['VolumeId']}")
                    except Exception as e:
                        logger.error(f"Error detaching volume {volume['VolumeId']}: {str(e)}")
                        # If the volume is already detached, continue
                        if "is not attached" not in str(e):
                            raise
                    
                    new_volume_id = new_volumes.get(volume['Device'])
                    if new_volume_id:
                        logger.info(f"Attaching new volume {new_volume_id} to device {volume['Device']}")
                        try:
                            self.aws_client.attach_volume(
                                new_volume_id,
                                instance_id,
                                volume['Device']
                            )
                            # Wait for the volume to be attached
                            self.aws_client.wait_for_volume_attachment(new_volume_id)
                            logger.info(f"Attached new volume {new_volume_id}")
                        except Exception as e:
                            if "Attachment point" in str(e) and "is already in use" in str(e):
                                # If the device is already in use, try to force detach and retry
                                logger.info(f"Device {volume['Device']} is already in use, attempting to force detach...")
                                self.aws_client.force_detach_volume(volume['VolumeId'])
                                self.aws_client.wait_for_volume_detachment(volume['VolumeId'])
                                # Retry attachment
                                self.aws_client.attach_volume(
                                    new_volume_id,
                                    instance_id,
                                    volume['Device']
                                )
                                self.aws_client.wait_for_volume_attachment(new_volume_id)
                                logger.info(f"Successfully attached new volume {new_volume_id} after force detach")
                            else:
                                raise
            attach_duration = datetime.now() - attach_start
            logger.info(f"Completed volume detachment and attachment in {attach_duration.total_seconds():.2f} seconds")

            # Start instance if it was running before
            if original_state == 'running':
                logger.info(f"Starting instance {instance_id}")
                self.aws_client.start_instance(instance_id)
                # Wait for instance to be running
                self.aws_client.wait_for_instance_state(instance_id, 'running')
                logger.info(f"Instance {instance_id} started successfully")

            # Display final volume changes after attachment
            display_volume_changes(current_volumes, ami_volumes, volume_devices, self.aws_client)

            total_duration = datetime.now() - start_time
            logger.info(f"Volume restore completed successfully in {total_duration.total_seconds():.2f} seconds")
            return instance_id

        except Exception as e:
            total_duration = datetime.now() - start_time
            logger.error(f"Error during volume restoration after {total_duration.total_seconds():.2f} seconds: {str(e)}")
            # Clean up created resources
            self._cleanup_created_resources(created_volumes)
            # Attempt rollback
            self._rollback_volume_restore(instance_id, snapshots)
            # Restore instance to original state
            self._restore_instance_state(instance_id, original_state)
            raise

    def _cleanup_created_resources(self, created_volumes: Dict[str, str]) -> None:
        """Clean up resources created during the restore process.
        
        Args:
            created_volumes: Dictionary mapping volume IDs to device names
        """
        for volume_id in created_volumes:
            try:
                logger.info(f"Cleaning up created volume {volume_id}")
                self.aws_client.delete_volume(volume_id)
            except Exception as e:
                logger.error(f"Error cleaning up volume {volume_id}: {str(e)}")
                # Continue with cleanup of other resources even if one fails

    def _rollback_volume_restore(self, instance_id: str, snapshots: Dict[str, str]):
        """Rollback volume restoration in case of errors."""
        try:
            # Stop instance
            self.aws_client.stop_instance(instance_id)

            # Restore volumes from snapshots
            for volume_id, snapshot_id in snapshots.items():
                new_volume_id = self.aws_client.create_volume_from_snapshot(
                    snapshot_id,
                    self.aws_client.get_instance_by_id(instance_id)['Placement']['AvailabilityZone']
                )
                self.aws_client.attach_volume(new_volume_id, instance_id, '/dev/sda1')

            # Start instance
            self.aws_client.start_instance(instance_id)
            
            logger.info("Volume restoration rollback completed successfully")
        except Exception as e:
            logger.error(f"Error during rollback: {str(e)}")
            raise

    def _restore_instance_state(self, instance_id: str, original_state: str) -> None:
        """Restore instance to its original state.
        
        Args:
            instance_id: The ID of the instance
            original_state: The original state of the instance ('running' or 'stopped')
        """
        try:
            current_instance = self.aws_client.get_instance_by_id(instance_id)
            current_state = current_instance['State']['Name']
            
            if original_state == 'running' and current_state == 'stopped':
                logger.info(f"Restoring instance {instance_id} to running state")
                self.aws_client.start_instance(instance_id)
                self.aws_client.wait_for_instance_state(instance_id, 'running')
            elif original_state == 'stopped' and current_state == 'running':
                logger.info(f"Restoring instance {instance_id} to stopped state")
                self.aws_client.stop_instance(instance_id)
                self.aws_client.wait_for_instance_state(instance_id, 'stopped')
        except Exception as e:
            logger.error(f"Error restoring instance state: {str(e)}")
            # Don't raise the exception as this is cleanup code

    def generate_restore_report(self, instance_id: str, backup_file: str,
                              restore_type: str, new_instance_id: Optional[str] = None) -> str:
        """Generate a concise restoration report focusing on changes."""
        try:
            with open(backup_file, 'r') as f:
                backup_data = json.load(f)
                original_instance = backup_data['InstanceDetails']
                instance_name = backup_data['InstanceName']

            # If this is a full restore with a new instance, wait for it to be available
            if new_instance_id and restore_type == 'full':
                logger.info(f"Waiting for new instance {new_instance_id} to be available...")
                self.aws_client.wait_for_instance_state(new_instance_id, 'running')
                time.sleep(60)  # Additional wait time to ensure instance is fully available in AWS

            current_instance = self.aws_client.get_instance_by_id(
                new_instance_id if new_instance_id else instance_id
            )

            # Get volume changes
            original_volumes = {v['Device']: v['VolumeId'] for v in original_instance.get('Volumes', [])}
            current_volumes = {v['Device']: v['VolumeId'] for v in current_instance.get('Volumes', [])}
            
            volume_changes = {}
            for device, current_vol_id in current_volumes.items():
                original_vol_id = original_volumes.get(device)
                if original_vol_id != current_vol_id:
                    volume_changes[device] = {
                        'previous': original_vol_id,
                        'current': current_vol_id
                    }

            # Create concise report
            report = {
                'timestamp': datetime.now().isoformat(),
                'restore_type': restore_type,
                'instance_name': instance_name,
                'instance_id': instance_id,
                'new_instance_id': new_instance_id if new_instance_id else None,
                'changes': {
                    'volumes': volume_changes,
                    'state': {
                        'previous': original_instance['State']['Name'],
                        'current': current_instance['State']['Name']
                    } if original_instance['State']['Name'] != current_instance['State']['Name'] else None
                }
            }

            # Remove empty changes
            if not report['changes']['volumes']:
                del report['changes']['volumes']
            if not report['changes']['state']:
                del report['changes']['state']
            if not report['changes']:
                del report['changes']

            report_file = self.backup_dir / f"restore_report_{instance_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(report_file, 'w') as f:
                json.dump(report, f, indent=2, default=str)

            return str(report_file)
        except Exception as e:
            logger.error(f"Error generating restore report: {str(e)}")
            raise 