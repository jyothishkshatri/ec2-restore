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
            
            # Get current volumes
            current_volumes = self.aws_client.get_instance_volumes(instance_id, is_ami=False)
            logger.info(f"Found {len(current_volumes)} current volumes on instance {instance_id}")
            
            # Display initial volume configuration
            logger.info("Displaying current volume configuration...")
            display_volume_changes(current_volumes, ami_volumes, volume_devices, self.aws_client)
            
            # Create snapshots of current volumes
            snapshot_start = datetime.now()
            for volume in current_volumes:
                if volume['Device'] in volume_devices:
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
                    # Get volume type from AMI volume
                    volume_type = volume.get('VolumeType', 'gp3')  # Default to gp3 if not specified
                    logger.info(f"Using volume type {volume_type} from AMI")
                    
                    new_volume_id = self.aws_client.create_volume_from_snapshot(
                        volume['VolumeId'],  # This is the snapshot ID from the AMI
                        instance['Placement']['AvailabilityZone'],
                        volume_type  # Use the volume type from AMI
                    )
                    new_volumes[volume['Device']] = new_volume_id
                    created_volumes[new_volume_id] = volume['Device']  # Track created volume
                    volume['NewVolumeId'] = new_volume_id  # Store the new volume ID in the AMI volume info
                    logger.info(f"Created new volume {new_volume_id} from snapshot {volume['VolumeId']} with type {volume_type}")
                    
                    # Wait for the new volume to be available
                    logger.info(f"Waiting for new volume {new_volume_id} to be available...")
                    if not self.aws_client.wait_for_volume_available(new_volume_id):
                        raise Exception(f"New volume {new_volume_id} failed to become available")
                    logger.info(f"New volume {new_volume_id} is now available")
            volume_duration = datetime.now() - volume_start
            logger.info(f"Created {len(new_volumes)} new volumes in {volume_duration.total_seconds():.2f} seconds")

            # Display volume changes before proceeding with attachment
            logger.info("Displaying volume changes before attachment...")
            display_volume_changes(current_volumes, ami_volumes, volume_devices, self.aws_client)

            # Stop instance if it's running
            if original_state == 'running':
                logger.info(f"Stopping instance {instance_id}")
                self.aws_client.stop_instance(instance_id)
                # Wait for instance to be stopped
                self.aws_client.wait_for_instance_state(instance_id, 'stopped')
                logger.info(f"Instance {instance_id} stopped successfully")

            # First, detach all old volumes
            detach_start = datetime.now()
            for volume in current_volumes:
                if volume['Device'] in volume_devices:
                    logger.info(f"Detaching volume {volume['VolumeId']} from device {volume['Device']}")
                    try:
                        # Check if volume is already detached
                        vol_response = self.aws_client.ec2_client.describe_volumes(VolumeIds=[volume['VolumeId']])
                        if vol_response['Volumes']:
                            vol_state = vol_response['Volumes'][0]
                            if vol_state['State'] == 'in-use':
                                self.aws_client.detach_volume(volume['VolumeId'])
                                # Wait for volume to be detached
                                if not self.aws_client.wait_for_volume_detached(volume['VolumeId']):
                                    logger.error(f"Failed to detach volume {volume['VolumeId']}")
                                    raise Exception(f"Failed to detach volume {volume['VolumeId']}")
                                logger.info(f"Detached volume {volume['VolumeId']}")
                            else:
                                logger.info(f"Volume {volume['VolumeId']} is already detached")
                    except Exception as e:
                        logger.error(f"Error detaching volume {volume['VolumeId']}: {str(e)}")
                        # If the volume is already detached, continue
                        if "is not attached" not in str(e):
                            raise
            detach_duration = datetime.now() - detach_start
            logger.info(f"Completed volume detachment in {detach_duration.total_seconds():.2f} seconds")

            # Then, attach all new volumes
            attach_start = datetime.now()
            for volume in current_volumes:
                if volume['Device'] in volume_devices:
                    new_volume_id = new_volumes.get(volume['Device'])
                    if new_volume_id:
                        logger.info(f"Attaching new volume {new_volume_id} to device {volume['Device']}")
                        try:
                            # The attach_volume method now handles device availability
                            self.aws_client.attach_volume(
                                new_volume_id,
                                instance_id,
                                volume['Device']
                            )
                            
                            # Wait for the volume to be attached
                            if not self.aws_client.wait_for_volume_attached(
                                new_volume_id,
                                instance_id,
                                volume['Device']
                            ):
                                logger.error(f"Failed to attach volume {new_volume_id}")
                                raise Exception(f"Failed to attach volume {new_volume_id}")
                            logger.info(f"Attached new volume {new_volume_id}")
                        except Exception as e:
                            logger.error(f"Error attaching volume {new_volume_id}: {str(e)}")
                            raise
            attach_duration = datetime.now() - attach_start
            logger.info(f"Completed volume attachment in {attach_duration.total_seconds():.2f} seconds")

            # Start instance if it was running before
            if original_state == 'running':
                logger.info(f"Starting instance {instance_id}")
                self.aws_client.start_instance(instance_id)
                # Wait for instance to be running
                self.aws_client.wait_for_instance_state(instance_id, 'running')
                logger.info(f"Instance {instance_id} started successfully")

            # Display final volume changes after attachment
            logger.info("Displaying final volume configuration...")
            display_volume_changes(current_volumes, ami_volumes, volume_devices, self.aws_client)

            # Generate restore report with updated volume information
            logger.info("Generating restore report...")
            self.generate_restore_report(
                instance_id=instance_id,
                restore_type='volume',
                ami_id=ami_id,
                current_volumes=current_volumes,
                ami_volumes=ami_volumes,
                backup_file=backup_file
            )

            total_duration = datetime.now() - start_time
            logger.info(f"Volume restore completed successfully in {total_duration.total_seconds():.2f} seconds")
            return instance_id

        except Exception as e:
            total_duration = datetime.now() - start_time
            logger.error(f"Error during volume restoration after {total_duration.total_seconds():.2f} seconds: {str(e)}")
            # Clean up created resources
            self._cleanup_created_resources(created_volumes, snapshots)
            # Attempt rollback
            self._rollback_volume_restore(instance_id, snapshots)
            # Restore instance to original state
            self._restore_instance_state(instance_id, original_state)
            raise

    def _cleanup_created_resources(self, created_volumes: Dict[str, str], snapshots: Optional[Dict[str, str]] = None) -> None:
        """Clean up resources created during the restore process.
        
        Args:
            created_volumes: Dictionary mapping volume IDs to device names
            snapshots: Dictionary mapping volume IDs to snapshot IDs
        """
        # Clean up volumes
        for volume_id in created_volumes:
            try:
                logger.info(f"Cleaning up created volume {volume_id}")
                self.aws_client.delete_volume(volume_id)
            except Exception as e:
                logger.error(f"Error cleaning up volume {volume_id}: {str(e)}")
                # Continue with cleanup of other resources even if one fails

        # Clean up snapshots if provided
        if snapshots:
            for volume_id, snapshot_id in snapshots.items():
                try:
                    logger.info(f"Cleaning up snapshot {snapshot_id}")
                    self.aws_client.delete_snapshot(snapshot_id)
                except Exception as e:
                    logger.error(f"Error cleaning up snapshot {snapshot_id}: {str(e)}")
                    # Continue with cleanup of other resources even if one fails

    def _rollback_volume_restore(self, instance_id: str, snapshots: Dict[str, str]):
        """Rollback volume restoration in case of errors."""
        try:
            # Stop instance
            self.aws_client.stop_instance(instance_id)
            self.aws_client.wait_for_instance_state(instance_id, 'stopped')

            # Get instance details for availability zone
            instance = self.aws_client.get_instance_by_id(instance_id)
            az = instance['Placement']['AvailabilityZone']

            # Restore volumes from snapshots
            for volume_id, snapshot_id in snapshots.items():
                try:
                    # Get the original device name
                    vol_response = self.aws_client.ec2_client.describe_volumes(VolumeIds=[volume_id])
                    if vol_response['Volumes']:
                        original_device = vol_response['Volumes'][0]['Attachments'][0]['Device']
                    else:
                        logger.error(f"Could not find original device for volume {volume_id}")
                        continue

                    # Create new volume from snapshot
                    new_volume_id = self.aws_client.create_volume_from_snapshot(
                        snapshot_id,
                        az,
                        'gp3'  # Use gp3 for rollback volumes
                    )

                    # Wait for volume to be available
                    if not self.aws_client.wait_for_volume_available(new_volume_id):
                        raise Exception(f"Volume {new_volume_id} failed to become available")

                    # Attach volume to original device
                    self.aws_client.attach_volume(new_volume_id, instance_id, original_device)
                    
                    # Wait for attachment
                    if not self.aws_client.wait_for_volume_attached(new_volume_id, instance_id, original_device):
                        raise Exception(f"Failed to attach volume {new_volume_id}")

                except Exception as e:
                    logger.error(f"Error during rollback for volume {volume_id}: {str(e)}")
                    continue

            # Start instance
            self.aws_client.start_instance(instance_id)
            self.aws_client.wait_for_instance_state(instance_id, 'running')
            
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

    def generate_restore_report(self, instance_id: str, restore_type: str, ami_id: str, new_instance_id: Optional[str] = None, current_volumes: Optional[List[Dict]] = None, ami_volumes: Optional[List[Dict]] = None, backup_file: Optional[str] = None) -> str:
        """Generate a restore report with details about the restoration process."""
        try:
            # Create backup directory if it doesn't exist
            os.makedirs(self.backup_dir, exist_ok=True)
            
            # Load previous metadata if backup file exists
            previous_metadata = None
            if backup_file and os.path.exists(backup_file):
                with open(backup_file, 'r') as f:
                    previous_metadata = json.load(f)
            
            # Get instance details for the appropriate instance
            target_instance_id = new_instance_id if restore_type == 'full' else instance_id
            instance = self.aws_client.get_instance_by_id(target_instance_id)
            
            # Get instance name from tags
            instance_name = None
            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Name':
                    instance_name = tag['Value']
                    break
            
            # Get current volumes if not provided
            if not current_volumes:
                current_volumes = self.aws_client.get_instance_volumes(target_instance_id, is_ami=False)
            
            # Prepare report data
            report_data = {
                'timestamp': datetime.now().isoformat(),
                'restore_type': restore_type,
                'source_instance': {
                    'id': instance_id,
                    'name': instance_name,
                    'state': instance['State']['Name'],
                    'type': instance['InstanceType'],
                    'az': instance['Placement']['AvailabilityZone']
                },
                'source_ami': {
                    'id': ami_id,
                    'volumes': ami_volumes if ami_volumes is not None else []
                },
                'previous_state': {
                    'instance': {
                        'id': instance_id,
                        'name': instance_name,
                        'state': previous_metadata['InstanceDetails']['State']['Name'] if previous_metadata else None,
                        'type': previous_metadata['InstanceDetails']['InstanceType'] if previous_metadata else None,
                        'az': previous_metadata['InstanceDetails']['Placement']['AvailabilityZone'] if previous_metadata else None
                    },
                    'volumes': []
                },
                'restore_details': {
                    'snapshots': [],
                    'new_volumes': [],
                    'volume_changes': []
                },
                'current_state': {
                    'instance': {
                        'id': target_instance_id,
                        'name': instance_name,
                        'state': instance['State']['Name'],
                        'type': instance['InstanceType'],
                        'az': instance['Placement']['AvailabilityZone']
                    },
                    'volumes': []
                }
            }
            
            # Add previous volume information from metadata
            if previous_metadata and 'InstanceDetails' in previous_metadata:
                prev_instance = previous_metadata['InstanceDetails']
                for block_device in prev_instance.get('BlockDeviceMappings', []):
                    if 'Ebs' in block_device:
                        report_data['previous_state']['volumes'].append({
                            'device': block_device['DeviceName'],
                            'volume_id': block_device['Ebs']['VolumeId'],
                            'type': block_device['Ebs'].get('VolumeType', 'gp3'),
                            'size': block_device['Ebs'].get('VolumeSize', 'N/A'),
                            'delete_on_termination': block_device['Ebs'].get('DeleteOnTermination', True)
                        })
            
            # Track volume changes and new volumes
            if current_volumes and ami_volumes:
                # Map volumes by device name
                prev_volumes_map = {vol['device']: vol for vol in report_data['previous_state']['volumes']}
                curr_volumes_map = {vol['Device']: vol for vol in current_volumes}
                ami_volumes_map = {vol['Device']: vol for vol in ami_volumes}
                
                # Track new volumes created from AMI snapshots and update current state
                for device, ami_vol in ami_volumes_map.items():
                    if device in curr_volumes_map:
                        curr_vol = curr_volumes_map[device]
                        # Add to new volumes list
                        report_data['restore_details']['new_volumes'].append({
                            'device': device,
                            'volume_id': curr_vol['VolumeId'],
                            'snapshot_id': ami_vol['VolumeId'],
                            'type': curr_vol.get('VolumeType', 'gp3'),
                            'size': curr_vol.get('Size', 'N/A')
                        })
                        # Add to current state volumes
                        report_data['current_state']['volumes'].append({
                            'Device': device,
                            'VolumeId': curr_vol['VolumeId'],
                            'Size': curr_vol.get('Size', 'N/A'),
                            'VolumeType': curr_vol.get('VolumeType', 'gp3'),
                            'DeleteOnTermination': curr_vol.get('DeleteOnTermination', True)
                        })
                
                # Track volume changes
                for device in set(list(prev_volumes_map.keys()) + list(curr_volumes_map.keys())):
                    prev_vol = prev_volumes_map.get(device, {})
                    curr_vol = curr_volumes_map.get(device, {})
                    
                    if device in curr_volumes_map:
                        change_info = {
                            'device': device,
                            'previous_volume_id': prev_vol.get('volume_id', 'N/A'),
                            'new_volume_id': curr_vol['VolumeId'],
                            'type': curr_vol.get('VolumeType', 'gp3'),
                            'size': curr_vol.get('Size', 'N/A')
                        }
                        report_data['restore_details']['volume_changes'].append(change_info)
            
            # Generate report filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_filename = f"restore_report_{instance_id}_{timestamp}.json"
            report_path = os.path.join(self.backup_dir, report_filename)
            
            # Write report to file
            with open(report_path, 'w') as f:
                json.dump(report_data, f, indent=2)
            
            logger.info(f"Restore report generated: {report_path}")
            return report_path
            
        except Exception as e:
            logger.error(f"Error generating restore report: {str(e)}")
            raise

    def restore(self, instance_id: str, ami_id: str, restore_type: str = 'full', volume_devices: Optional[List[str]] = None) -> str:
        """Restore an instance from an AMI."""
        start_time = datetime.now()
        logger.info(f"Starting {restore_type} restore for instance {instance_id} from AMI {ami_id}")
        
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
            
            # Get current volumes for report
            current_volumes = self.aws_client.get_instance_volumes(instance_id, is_ami=False)
            logger.info(f"Found {len(current_volumes)} current volumes on instance {instance_id}")
            
            # Get AMI volumes for report
            ami_volumes = self.aws_client.get_instance_volumes(ami_id, is_ami=True)
            logger.info(f"Found {len(ami_volumes)} volumes in AMI {ami_id}")
            
            # Perform the appropriate restore
            if restore_type == 'full':
                new_instance_id = self.full_instance_restore(instance_id, ami_id)
                # Get current volumes for the new instance
                new_instance_volumes = self.aws_client.get_instance_volumes(new_instance_id, is_ami=False)
                logger.info(f"Found {len(new_instance_volumes)} volumes on new instance {new_instance_id}")
                
                # Generate restore report with new instance ID
                self.generate_restore_report(
                    instance_id=instance_id,
                    restore_type='full',
                    ami_id=ami_id,
                    new_instance_id=new_instance_id,
                    current_volumes=new_instance_volumes,
                    ami_volumes=ami_volumes,  # Pass the AMI volumes we retrieved earlier
                    backup_file=backup_file
                )
                return new_instance_id
            elif restore_type == 'volume':
                if not volume_devices:
                    raise ValueError("Volume devices must be specified for volume restore")
                
                # Perform volume restore
                self.volume_restore(instance_id, ami_id, volume_devices)
                
                # Get updated volumes after restore
                updated_volumes = self.aws_client.get_instance_volumes(instance_id, is_ami=False)
                logger.info(f"Found {len(updated_volumes)} volumes after restore")
                
                # Generate restore report
                self.generate_restore_report(
                    instance_id=instance_id,
                    restore_type='volume',
                    ami_id=ami_id,
                    current_volumes=updated_volumes,
                    ami_volumes=ami_volumes,  # Pass the AMI volumes we retrieved earlier
                    backup_file=backup_file
                )
                return instance_id
            else:
                raise ValueError(f"Invalid restore type: {restore_type}")
                
        except Exception as e:
            total_duration = datetime.now() - start_time
            logger.error(f"Error during {restore_type} restore after {total_duration.total_seconds():.2f} seconds: {str(e)}")
            raise