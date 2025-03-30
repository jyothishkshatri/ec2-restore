import boto3
import logging
from typing import Dict, List, Optional
from botocore.exceptions import ClientError
import time

logger = logging.getLogger(__name__)

class AWSClient:
    def __init__(self, profile_name: Optional[str] = None, region: Optional[str] = None):
        """Initialize AWS client with optional profile and region."""
        try:
            self.session = boto3.Session(profile_name=profile_name, region_name=region)
            self.ec2_client = self.session.client('ec2')
            self.ec2_resource = self.session.resource('ec2')
            logger.info(f"Initialized AWS client with profile: {profile_name}, region: {region}")
        except Exception as e:
            logger.error(f"Error initializing AWS client: {str(e)}")
            raise

    def get_instance_by_id(self, instance_id: str) -> Dict:
        """Get instance details by ID."""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            return response['Reservations'][0]['Instances'][0]
        except ClientError as e:
            logger.error(f"Error getting instance {instance_id}: {str(e)}")
            raise

    def get_instance_by_name(self, instance_name: str) -> Dict:
        """Get instance details by name tag."""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[{'Name': 'tag:Name', 'Values': [instance_name]}]
            )
            if not response['Reservations']:
                raise ValueError(f"No instance found with name {instance_name}")
            return response['Reservations'][0]['Instances'][0]
        except ClientError as e:
            logger.error(f"Error getting instance by name {instance_name}: {str(e)}")
            raise

    def get_instance_amis(self, instance_id: str, max_amis: int = 5) -> List[Dict]:
        """Get recent AMIs for an instance."""
        try:
            # First try to get instance details to find its name tag
            instance = self.get_instance_by_id(instance_id)
            instance_name = None
            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Name':
                    instance_name = tag['Value']
                    break

            # Get the account ID from the instance reservation
            account_id = instance.get('OwnerId', 'self')

            # Search for AMIs by name
            filters = [
                {'Name': 'state', 'Values': ['available']}
            ]

            if instance_name:
                filters.append({
                    'Name': 'tag:Name',
                    'Values': [instance_name, f"{instance_name}-*"]
                })

            response = self.ec2_client.describe_images(
                Filters=filters,
                Owners=['self']
            )
            
            # Sort by creation date and limit to max_amis
            amis = sorted(
                response['Images'],
                key=lambda x: x['CreationDate'],
                reverse=True
            )[:max_amis]
            return amis
        except ClientError as e:
            logger.error(f"Error getting AMIs for instance {instance_id}: {str(e)}")
            raise

    def get_instance_volumes(self, resource_id: str, is_ami: bool = False) -> List[Dict]:
        """Get all volumes from an instance or AMI.
        
        Args:
            resource_id: The ID of the instance or AMI
            is_ami: Whether the resource_id is an AMI ID (True) or instance ID (False)
        """
        try:
            if is_ami:
                # Get volumes from AMI's block device mappings
                response = self.ec2_client.describe_images(ImageIds=[resource_id])
                if not response['Images']:
                    raise ValueError(f"No AMI found with ID {resource_id}")
                
                ami = response['Images'][0]
                volumes = []
                
                for mapping in ami.get('BlockDeviceMappings', []):
                    if 'Ebs' in mapping:
                        ebs = mapping['Ebs']
                        volume = {
                            'Device': mapping['DeviceName'],
                            'VolumeId': ebs.get('SnapshotId', ''),  # For AMIs, this is the snapshot ID
                            'Size': ebs.get('VolumeSize', 0),
                            'VolumeType': ebs.get('VolumeType', 'gp3'),
                            'DeleteOnTermination': ebs.get('DeleteOnTermination', True),
                            'IsSnapshot': True  # Flag to indicate this is a snapshot ID
                        }
                        volumes.append(volume)
                
                return volumes
            else:
                # Get volumes from instance
                response = self.ec2_client.describe_instances(InstanceIds=[resource_id])
                if not response['Reservations']:
                    raise ValueError(f"No instance found with ID {resource_id}")
                
                instance = response['Reservations'][0]['Instances'][0]
                volumes = []
                
                for block_device in instance.get('BlockDeviceMappings', []):
                    if 'Ebs' in block_device:
                        ebs = block_device['Ebs']
                        volume = {
                            'Device': block_device['DeviceName'],
                            'VolumeId': ebs['VolumeId'],
                            'Size': ebs.get('VolumeSize', 0),
                            'VolumeType': ebs.get('VolumeType', 'gp3'),
                            'DeleteOnTermination': ebs.get('DeleteOnTermination', True),
                            'IsSnapshot': False  # Flag to indicate this is a volume ID
                        }
                        volumes.append(volume)
                
                return volumes
                
        except ClientError as e:
            logger.error(f"Error getting volumes for {'AMI' if is_ami else 'instance'} {resource_id}: {str(e)}")
            raise

    def create_volume_snapshot(self, volume_id: str, description: str) -> str:
        """Create a snapshot of a volume."""
        try:
            response = self.ec2_client.create_snapshot(
                VolumeId=volume_id,
                Description=description
            )
            return response['SnapshotId']
        except ClientError as e:
            logger.error(f"Error creating snapshot for volume {volume_id}: {str(e)}")
            raise

    def wait_for_snapshot_completion(self, snapshot_id: str, timeout: int = 300) -> None:
        """Wait for a snapshot to be completed.
        
        Args:
            snapshot_id: The ID of the snapshot to check
            timeout: Maximum time to wait in seconds (default: 300)
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
            state = response['Snapshots'][0]['State']
            if state == 'completed':
                return
            elif state == 'error':
                raise ValueError(f"Snapshot {snapshot_id} failed to complete")
            time.sleep(5)  # Wait 5 seconds before checking again
        
        raise TimeoutError(f"Snapshot {snapshot_id} did not complete within {timeout} seconds")

    def wait_for_volume_availability(self, volume_id: str, timeout: int = 300) -> None:
        """Wait for a volume to be available.
        
        Args:
            volume_id: The ID of the volume to check
            timeout: Maximum time to wait in seconds (default: 300)
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            state = response['Volumes'][0]['State']
            if state == 'available':
                return
            elif state == 'error':
                raise ValueError(f"Volume {volume_id} failed to become available")
            time.sleep(5)  # Wait 5 seconds before checking again
        
        raise TimeoutError(f"Volume {volume_id} did not become available within {timeout} seconds")

    def delete_volume(self, volume_id: str) -> None:
        """Delete a volume."""
        try:
            self.ec2_client.delete_volume(VolumeId=volume_id)
        except ClientError as e:
            logger.error(f"Error deleting volume {volume_id}: {str(e)}")
            raise

    def create_volume_from_snapshot(self, snapshot_id: str, availability_zone: str,
                                  volume_type: str = 'gp3') -> str:
        """Create a new volume from a snapshot."""
        try:
            # Wait for snapshot to be completed
            self.wait_for_snapshot_completion(snapshot_id)
            
            response = self.ec2_client.create_volume(
                SnapshotId=snapshot_id,
                AvailabilityZone=availability_zone,
                VolumeType=volume_type
            )
            volume_id = response['VolumeId']
            
            # Wait for volume to be available
            self.wait_for_volume_availability(volume_id)
            
            return volume_id
        except ClientError as e:
            logger.error(f"Error creating volume from snapshot {snapshot_id}: {str(e)}")
            raise

    def attach_volume(self, volume_id: str, instance_id: str, device: str) -> None:
        """Attach a volume to an instance."""
        try:
            self.ec2_client.attach_volume(
                VolumeId=volume_id,
                InstanceId=instance_id,
                Device=device
            )
        except ClientError as e:
            logger.error(f"Error attaching volume {volume_id} to instance {instance_id}: {str(e)}")
            raise

    def create_instance(self, image_id: str, instance_type: str,
                       subnet_id: Optional[str] = None,
                       security_group_ids: Optional[List[str]] = None,
                       key_name: Optional[str] = None,
                       network_interfaces: Optional[List[Dict]] = None) -> str:
        """Create a new EC2 instance."""
        try:
            # Prepare launch parameters
            launch_params = {
                'ImageId': image_id,
                'InstanceType': instance_type,
                'MinCount': 1,
                'MaxCount': 1
            }

            # Add network configuration
            if network_interfaces:
                launch_params['NetworkInterfaces'] = network_interfaces
            else:
                if subnet_id:
                    launch_params['SubnetId'] = subnet_id
                if security_group_ids:
                    launch_params['SecurityGroupIds'] = security_group_ids

            # Add key pair if specified
            if key_name:
                launch_params['KeyName'] = key_name

            # Launch instance
            response = self.ec2_client.run_instances(**launch_params)
            instance_id = response['Instances'][0]['InstanceId']
            
            # Wait for instance to be running
            self.wait_for_instance_state(instance_id, 'running')
            
            return instance_id
        except Exception as e:
            logger.error(f"Error creating instance: {str(e)}")
            raise

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate an EC2 instance."""
        try:
            self.ec2_client.terminate_instances(InstanceIds=[instance_id])
        except ClientError as e:
            logger.error(f"Error terminating instance {instance_id}: {str(e)}")
            raise

    def stop_instance(self, instance_id: str) -> None:
        """Stop an EC2 instance."""
        try:
            self.ec2_client.stop_instances(InstanceIds=[instance_id])
        except ClientError as e:
            logger.error(f"Error stopping instance {instance_id}: {str(e)}")
            raise

    def start_instance(self, instance_id: str) -> None:
        """Start an EC2 instance."""
        try:
            self.ec2_client.start_instances(InstanceIds=[instance_id])
        except ClientError as e:
            logger.error(f"Error starting instance {instance_id}: {str(e)}")
            raise

    def modify_network_interface_attribute(self, network_interface_id: str,
                                        attachment_id: Optional[str] = None,
                                        delete_on_termination: Optional[bool] = None) -> None:
        """Modify network interface attributes."""
        try:
            if delete_on_termination is not None:
                self.ec2_client.modify_network_interface_attribute(
                    NetworkInterfaceId=network_interface_id,
                    Attachment={
                        'AttachmentId': attachment_id,
                        'DeleteOnTermination': delete_on_termination
                    }
                )
                logger.info(f"Modified network interface {network_interface_id} delete on termination to {delete_on_termination}")
        except Exception as e:
            logger.error(f"Error modifying network interface {network_interface_id}: {str(e)}")
            raise

    def wait_for_instance_state(self, instance_id: str, desired_state: str, timeout: int = 300) -> None:
        """Wait for an instance to reach a desired state.
        
        Args:
            instance_id: The ID of the instance to check
            desired_state: The desired state ('running', 'stopped', etc.)
            timeout: Maximum time to wait in seconds (default: 300)
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            instance = self.get_instance_by_id(instance_id)
            if instance['State']['Name'] == desired_state:
                return
            time.sleep(5)  # Wait 5 seconds before checking again
        
        raise TimeoutError(f"Instance {instance_id} did not reach state {desired_state} within {timeout} seconds")

    def detach_volume(self, volume_id: str) -> None:
        """Detach a volume from an instance."""
        try:
            self.ec2_client.detach_volume(VolumeId=volume_id)
        except ClientError as e:
            logger.error(f"Error detaching volume {volume_id}: {str(e)}")
            raise

    def wait_for_volume_detachment(self, volume_id: str, timeout: int = 300) -> None:
        """Wait for a volume to be detached.
        
        Args:
            volume_id: The ID of the volume to check
            timeout: Maximum time to wait in seconds (default: 300)
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            state = response['Volumes'][0]['State']
            if state == 'available':
                return
            elif state == 'error':
                raise ValueError(f"Volume {volume_id} failed to detach")
            time.sleep(5)  # Wait 5 seconds before checking again
        
        raise TimeoutError(f"Volume {volume_id} did not detach within {timeout} seconds")

    def create_instance_with_config(self, launch_params: Dict) -> str:
        """Create a new EC2 instance with full configuration."""
        try:
            response = self.ec2_client.run_instances(**launch_params)
            instance_id = response['Instances'][0]['InstanceId']
            
            # Wait for instance to be running
            self.wait_for_instance_state(instance_id, 'running')
            
            return instance_id
        except Exception as e:
            logger.error(f"Error creating instance with config: {str(e)}")
            raise

    def create_tags(self, resource_id: str, tags: List[Dict]) -> None:
        """Create tags for a resource."""
        try:
            self.ec2_client.create_tags(
                Resources=[resource_id],
                Tags=tags
            )
            logger.info(f"Created tags for resource {resource_id}")
        except Exception as e:
            logger.error(f"Error creating tags for resource {resource_id}: {str(e)}")
            raise

    def modify_instance_attribute(self, instance_id: str, attribute: str, value: Dict) -> None:
        """Modify an instance attribute."""
        try:
            self.ec2_client.modify_instance_attribute(
                InstanceId=instance_id,
                **{attribute: value}
            )
            logger.info(f"Modified {attribute} for instance {instance_id}")
        except Exception as e:
            logger.error(f"Error modifying {attribute} for instance {instance_id}: {str(e)}")
            raise

    def wait_for_instance_availability(self, instance_id: str, timeout: int = 300) -> None:
        """Wait for instance to be available in AWS.
        
        Args:
            instance_id: The ID of the instance to wait for
            timeout: Maximum time to wait in seconds (default: 5 minutes)
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
                if response['Reservations'] and response['Reservations'][0]['Instances']:
                    instance = response['Reservations'][0]['Instances'][0]
                    if instance['State']['Name'] == 'running':
                        logger.info(f"Instance {instance_id} is now available in AWS")
                        return
            except Exception as e:
                logger.debug(f"Instance {instance_id} not yet available: {str(e)}")
            time.sleep(10)  # Wait 10 seconds before checking again
        
        raise TimeoutError(f"Instance {instance_id} did not become available within {timeout} seconds")

    def wait_for_volume_available(self, volume_id: str, timeout: int = 300) -> None:
        """Wait for a volume to become available.
        
        Args:
            volume_id: The ID of the volume to wait for
            timeout: Maximum time to wait in seconds (default: 300)
            
        Raises:
            TimeoutError: If the volume does not become available within the timeout period
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                volume = response['Volumes'][0]
                if volume['State'] == 'available':
                    logger.info(f"Volume {volume_id} is now available")
                    return
                elif volume['State'] == 'error':
                    raise ValueError(f"Volume {volume_id} is in error state")
                logger.info(f"Waiting for volume {volume_id} to be available... Current state: {volume['State']}")
            except Exception as e:
                logger.error(f"Error checking volume state: {str(e)}")
            time.sleep(10)  # Wait 10 seconds before checking again
        
        raise TimeoutError(f"Volume {volume_id} did not become available within {timeout} seconds")

    def wait_for_volume_attachment(self, volume_id: str, timeout: int = 300) -> None:
        """Wait for a volume to be attached to an instance.
        
        Args:
            volume_id: The ID of the volume to wait for
            timeout: Maximum time to wait in seconds (default: 300)
            
        Raises:
            TimeoutError: If the volume does not become attached within the timeout period
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                volume = response['Volumes'][0]
                if volume['State'] == 'in-use':
                    logger.info(f"Volume {volume_id} is now attached")
                    return
                elif volume['State'] == 'error':
                    raise ValueError(f"Volume {volume_id} is in error state")
                logger.info(f"Waiting for volume {volume_id} to be attached... Current state: {volume['State']}")
            except Exception as e:
                logger.error(f"Error checking volume state: {str(e)}")
            time.sleep(10)  # Wait 10 seconds before checking again
        
        raise TimeoutError(f"Volume {volume_id} did not become attached within {timeout} seconds")

    def force_detach_volume(self, volume_id: str) -> None:
        """Force detach a volume from an instance.
        
        Args:
            volume_id: The ID of the volume to force detach
        """
        try:
            # Get volume details to find the instance ID
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            volume = response['Volumes'][0]
            
            if volume['State'] == 'in-use' and volume['Attachments']:
                instance_id = volume['Attachments'][0]['InstanceId']
                device = volume['Attachments'][0]['Device']
                
                # Force detach the volume
                self.ec2_client.detach_volume(
                    VolumeId=volume_id,
                    InstanceId=instance_id,
                    Device=device,
                    Force=True
                )
                logger.info(f"Force detached volume {volume_id} from instance {instance_id}")
            else:
                logger.info(f"Volume {volume_id} is not attached to any instance")
        except Exception as e:
            logger.error(f"Error force detaching volume {volume_id}: {str(e)}")
            raise 