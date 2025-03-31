import logging
import time
from typing import Dict, List, Optional
from botocore.exceptions import ClientError, WaiterError
from rich.progress import Progress
import boto3 # Ensure boto3 is imported

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

    def get_instance_details(self, instance_id: str) -> Dict:
        """Get instance details by ID."""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            if not response['Reservations'] or not response['Reservations'][0]['Instances']:
                 raise ValueError(f"Instance {instance_id} not found.")
            return response['Reservations'][0]['Instances'][0]
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidInstanceID.NotFound':
                 logger.error(f"Instance {instance_id} not found.")
                 raise ValueError(f"Instance {instance_id} not found.") from e
            logger.error(f"Error getting instance {instance_id}: {str(e)}")
            raise

    def get_volume_details(self, volume_id: str) -> Dict:
        """Get volume details by ID."""
        try:
            response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
            if not response['Volumes']:
                 raise ValueError(f"Volume {volume_id} not found.")
            return response['Volumes'][0]
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidVolume.NotFound':
                 logger.error(f"Volume {volume_id} not found.")
                 raise ValueError(f"Volume {volume_id} not found.") from e
            logger.error(f"Error getting volume {volume_id}: {str(e)}")
            raise

    def get_instance_amis(self, instance_id: str, max_amis: int = 5) -> List[Dict]:
        """Get recent AMIs potentially related to an instance."""
        try:
            # First try to get instance details to find its name tag
            instance = self.get_instance_details(instance_id)
            instance_name = None
            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Name':
                    instance_name = tag['Value']
                    break

            # Search for AMIs owned by the account, potentially filtered by name
            filters = [
                {'Name': 'state', 'Values': ['available']},
                # Consider adding a filter based on the source instance ID if AMIs are tagged appropriately
                # {'Name': 'tag:SourceInstanceId', 'Values': [instance_id]}
            ]

            # Filter by name tag if available - this might be broad
            if instance_name:
                # Example patterns - adjust based on actual naming conventions
                filters.append({
                    'Name': 'tag:Name',
                    'Values': [f"{instance_name}*", f"AMI for {instance_name}*"]
                })
            else:
                 # If no name, maybe filter by instance ID in description or tags if convention exists
                 # filters.append({'Name': 'description', 'Values': [f'* for instance {instance_id}*']})
                 pass # Rely solely on ownership and manual selection if no name

            logger.debug(f"Searching for AMIs with filters: {filters}")

            response = self.ec2_client.describe_images(
                Filters=filters,
                Owners=['self'] # Typically you want AMIs you own
            )

            # Sort by creation date
            amis = sorted(
                response['Images'],
                key=lambda x: x['CreationDate'],
                reverse=True
            )

            # Further filter if the initial search was too broad (e.g., check description/tags)
            # Example: Filter AMIs that likely belong to the instance based on naming or tags
            # relevant_amis = [ami for ami in amis if instance_id in ami.get('Description', '') or instance_id in str(ami.get('Tags', []))]
            # amis = relevant_amis

            # Limit to max_amis
            amis = amis[:max_amis]

            logger.info(f"Found {len(amis)} potential AMIs for instance {instance_id}.")
            return amis
        except ClientError as e:
            logger.error(f"Error getting AMIs for instance {instance_id}: {str(e)}")
            raise
        except Exception as e: # Catch other potential errors
            logger.error(f"Unexpected error getting AMIs for instance {instance_id}: {str(e)}")
            raise

    def wait_for_snapshot_completion(self, snapshot_id: str, delay: int = 15, max_attempts: int = 40) -> bool:
        """Wait for a snapshot to reach the 'completed' state using a waiter."""
        try:
            waiter = self.ec2_client.get_waiter('snapshot_completed')
            logger.info(f"Waiting for snapshot {snapshot_id} to complete...")
            waiter.wait(
                SnapshotIds=[snapshot_id],
                WaiterConfig={'Delay': delay, 'MaxAttempts': max_attempts}
            )
            logger.info(f"Snapshot {snapshot_id} completed.")
            return True
        except WaiterError as e:
            logger.error(f"Waiter failed or timed out waiting for snapshot {snapshot_id} to complete: {e}")
            return False
        except ClientError as e:
             logger.error(f"API error while waiting for snapshot {snapshot_id} to complete: {e}")
             return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while waiting for snapshot {snapshot_id} to complete: {e}")
            return False

    def wait_for_volume_availability(self, volume_id: str, delay: int = 10, max_attempts: int = 30) -> bool:
        """Wait for a volume to reach the 'available' state using a waiter."""
        try:
            waiter = self.ec2_client.get_waiter('volume_available')
            logger.info(f"Waiting for volume {volume_id} to become available...")
            waiter.wait(
                VolumeIds=[volume_id],
                WaiterConfig={'Delay': delay, 'MaxAttempts': max_attempts}
            )
            logger.info(f"Volume {volume_id} is now available.")
            return True
        except WaiterError as e:
            logger.error(f"Waiter failed or timed out waiting for volume {volume_id} to become available: {e}")
            return False
        except ClientError as e:
             logger.error(f"API error while waiting for volume {volume_id} to become available: {e}")
             return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while waiting for volume {volume_id} to become available: {e}")
            return False

    def create_volume_from_snapshot(self, snapshot_id: str, availability_zone: str,
                                  volume_type: str = 'gp3') -> str:
        """Create a new volume from a snapshot."""
        try:
            # Wait for snapshot to be completed
            if not self.wait_for_snapshot_completion(snapshot_id):
                 raise Exception(f"Snapshot {snapshot_id} did not complete in time.")

            logger.info(f"Creating volume from snapshot {snapshot_id} in AZ {availability_zone} with type {volume_type}")
            response = self.ec2_client.create_volume(
                SnapshotId=snapshot_id,
                AvailabilityZone=availability_zone,
                VolumeType=volume_type
            )
            volume_id = response['VolumeId']
            logger.info(f"Volume creation initiated: {volume_id}")

            # Wait for volume to be available
            if not self.wait_for_volume_availability(volume_id):
                 # Attempt cleanup if volume creation failed after initiation
                 logger.warning(f"Volume {volume_id} did not become available. Attempting deletion.")
                 try:
                     self.delete_volume(volume_id)
                 except Exception as del_e:
                     logger.error(f"Failed to clean up volume {volume_id} after availability timeout: {del_e}")
                 raise Exception(f"Volume {volume_id} did not become available in time.")

            logger.info(f"Volume {volume_id} created successfully and is available.")
            return volume_id
        except ClientError as e:
            logger.error(f"Error creating volume from snapshot {snapshot_id}: {str(e)}")
            raise
        except Exception as e: # Catch other errors like the ones raised above
            logger.error(f"Failed to create volume from snapshot {snapshot_id}: {str(e)}")
            raise

    def stop_instance(self, instance_id: str) -> None:
        """Stop an EC2 instance."""
        try:
            logger.info(f"Stopping instance {instance_id}...")
            self.ec2_client.stop_instances(InstanceIds=[instance_id])
            # Note: Waiting for 'stopped' state is handled separately if needed
        except ClientError as e:
            # Handle case where instance is already stopped
            if e.response['Error']['Code'] == 'IncorrectInstanceState':
                 logger.warning(f"Instance {instance_id} is already stopped or in a state that prevents stopping: {e}")
                 # Check current state to confirm if it's stopped
                 try:
                     details = self.get_instance_details(instance_id)
                     if details and details['State']['Name'] == 'stopped':
                         logger.info(f"Instance {instance_id} confirmed stopped.")
                         return # Treat as success
                 except Exception as check_e:
                      logger.error(f"Could not confirm instance state after stop error: {check_e}")
            logger.error(f"Error stopping instance {instance_id}: {str(e)}")
            raise

    def start_instance(self, instance_id):
        """Start an EC2 instance and wait for it to be running."""
        try:
            logging.info(f"Starting instance {instance_id}...")
            self.ec2_client.start_instances(InstanceIds=[instance_id])
            # Use waiter for instance running
            if not self.wait_for_instance_running(instance_id):
                raise WaiterError(name='instance_running', reason='Waiter failed or timed out after start command.', last_response=None)
            logging.info(f"Instance {instance_id} is running.")
            return True
        except ClientError as e:
             # Handle case where instance is already running
            if e.response['Error']['Code'] == 'IncorrectInstanceState':
                 logger.warning(f"Instance {instance_id} is already running or in a state that prevents starting: {e}")
                 try:
                     details = self.get_instance_details(instance_id)
                     if details and details['State']['Name'] == 'running':
                         logger.info(f"Instance {instance_id} confirmed running.")
                         return True # Treat as success
                 except Exception as check_e:
                      logger.error(f"Could not confirm instance state after start error: {check_e}")
            logger.error(f"Failed to start instance {instance_id}: {e}")
            return False
        except WaiterError as e:
            logger.error(f"Failed to wait for instance {instance_id} to run: {e}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while starting instance {instance_id}: {e}")
            return False

    def terminate_instance(self, instance_id):
        """Terminate an EC2 instance and wait for it to be terminated."""
        try:
            logging.info(f"Terminating instance {instance_id}...")
            self.ec2_client.terminate_instances(InstanceIds=[instance_id])
            # Use waiter for instance terminated
            if not self.wait_for_instance_state(instance_id, 'terminated'):
                 raise WaiterError(name='instance_terminated', reason='Waiter failed or timed out after terminate command.', last_response=None)
            logging.info(f"Instance {instance_id} terminated.")
            return True
        except (ClientError, WaiterError) as e:
            # Handle cases where instance might already be terminated or in a state preventing termination
            if 'InvalidInstanceID.NotFound' in str(e) or ('IncorrectInstanceState' in str(e) and 'terminated' in str(e)):
                 logging.warning(f"Instance {instance_id} might already be terminated or in a non-terminatable state: {e}")
                 # Confirm terminated state
                 try:
                     # This will raise ValueError if not found, which is caught below
                     details = self.get_instance_details(instance_id)
                     if details and details['State']['Name'] == 'terminated':
                         logging.info(f"Instance {instance_id} confirmed terminated.")
                         return True
                 except ValueError: # Instance not found
                      logging.info(f"Instance {instance_id} confirmed terminated (not found).")
                      return True
                 except Exception as check_e:
                      logging.error(f"Could not confirm instance state after terminate error: {check_e}")

            logging.error(f"Failed to terminate or wait for instance {instance_id}: {e}")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred while terminating instance {instance_id}: {e}")
            return False

    def create_instance(self, launch_args):
        """Launch an EC2 instance and return its ID."""
        try:
            logging.info(f"Launching instance with args: {launch_args}")
            instances = self.ec2_client.run_instances(**launch_args)
            instance_id = instances['Instances'][0]['InstanceId']
            logging.info(f"Instance creation initiated: {instance_id}")
            # Note: Waiting for 'running' state is handled separately after this call
            return instance_id
        except ClientError as e:
            logging.error(f"Failed to launch instance: {e}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during instance launch: {e}")
            return None

    def wait_for_instance_running(self, instance_id, delay=15, max_attempts=40):
        """Wait for an instance to reach the 'running' state using a waiter."""
        return self.wait_for_instance_state(instance_id, 'running', delay, max_attempts)


    def detach_network_interface(self, attachment_id, force=False):
        """Detach a network interface."""
        try:
            logging.info(f"Detaching network interface with attachment ID: {attachment_id} (Force={force})")
            self.ec2_client.detach_network_interface(AttachmentId=attachment_id, Force=force)
            # Note: Waiting for detachment completion is handled separately if needed
            logging.info(f"Detachment initiated for attachment ID: {attachment_id}")
            return True
        except ClientError as e:
            # Handle common case where it might already be detached or invalid
            if 'InvalidAttachmentID.NotFound' in str(e):
                logging.warning(f"Attachment ID {attachment_id} not found, likely already detached.")
                return True # Treat as success if goal is detachment
            logging.error(f"Failed to detach network interface attachment {attachment_id}: {e}")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred during ENI detachment: {e}")
            return False

    def wait_for_eni_detached(self, eni_id, instance_id=None, delay=10, max_attempts=18):
        """Wait for an ENI to be in 'available' state (detached)."""
        logging.info(f"Waiting for ENI {eni_id} to become available (detached)...")
        for attempt in range(max_attempts):
            try:
                response = self.ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
                if response['NetworkInterfaces']:
                    eni_status = response['NetworkInterfaces'][0]['Status']
                    attachment = response['NetworkInterfaces'][0].get('Attachment')
                    logging.debug(f"ENI {eni_id} status: {eni_status}, Attachment: {attachment} (Attempt {attempt + 1}/{max_attempts})")
                    if eni_status == 'available' and not attachment:
                        logging.info(f"ENI {eni_id} is available (detached).")
                        return True
                    # If instance_id is provided, check if it's detached specifically from that instance
                    if instance_id and attachment and attachment.get('InstanceId') == instance_id:
                         logging.debug(f"ENI {eni_id} still attached to target instance {instance_id}.")
                    elif instance_id and not attachment:
                         logging.info(f"ENI {eni_id} is detached from instance {instance_id} (Attachment is None).")
                         return True # Detached from the specific instance
                    elif not instance_id and not attachment:
                         logging.info(f"ENI {eni_id} is detached (Attachment is None).")
                         return True # Detached from any instance

                else:
                    logging.warning(f"ENI {eni_id} not found during wait.")
                    # Should not happen if called after confirming existence, treat as error or potential eventual consistency issue
                    return False # Or raise error? For now return False

            except ClientError as e:
                logging.error(f"API error waiting for ENI {eni_id} detachment: {e}")
                # Depending on the error, might want to retry or fail immediately
                if 'InvalidNetworkInterfaceID.NotFound' in str(e):
                     logging.error(f"ENI {eni_id} not found, cannot wait for detachment.")
                     return False
            except Exception as e:
                 logging.error(f"Unexpected error waiting for ENI {eni_id} detachment: {e}")
                 return False # Fail on unexpected errors

            time.sleep(delay)
        logging.error(f"Timeout waiting for ENI {eni_id} to detach.")
        return False


    def attach_network_interface(self, network_interface_id, instance_id, device_index):
        """Attach a network interface to an instance."""
        try:
            logging.info(f"Attaching ENI {network_interface_id} to instance {instance_id} at index {device_index}...")
            response = self.ec2_client.attach_network_interface(
                NetworkInterfaceId=network_interface_id,
                InstanceId=instance_id,
                DeviceIndex=device_index
            )
            attachment_id = response['AttachmentId']
            logging.info(f"ENI {network_interface_id} attachment initiated (Attachment ID: {attachment_id}).")
            # Note: Waiting for attachment completion handled separately (verify_eni_attached)
            return attachment_id
        except ClientError as e:
            logging.error(f"Failed to attach ENI {network_interface_id} to instance {instance_id}: {e}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during ENI attachment: {e}")
            return None

    def verify_eni_attached(self, eni_id, instance_id, delay=5, max_attempts=12):
        """Verify if an ENI is attached to a specific instance."""
        logging.info(f"Verifying attachment of ENI {eni_id} to instance {instance_id}...")
        for attempt in range(max_attempts):
            try:
                details = self.get_instance_details(instance_id)
                if details:
                    for ni in details.get('NetworkInterfaces', []):
                        if ni['NetworkInterfaceId'] == eni_id:
                            # Check attachment status as well
                            if ni.get('Attachment', {}).get('Status') == 'attached':
                                logging.info(f"ENI {eni_id} successfully verified as attached to {instance_id}.")
                                return True
                            else:
                                logging.debug(f"ENI {eni_id} found on {instance_id} but attachment status is {ni.get('Attachment', {}).get('Status')}")
                else:
                     logging.warning(f"Could not get details for instance {instance_id} during ENI verification.")
                     # Instance might still be initializing, retry

            except Exception as e:
                 logging.error(f"Error verifying ENI attachment for {eni_id} on {instance_id}: {e}")
                 # Decide whether to retry or fail based on error

            logging.debug(f"ENI {eni_id} not yet verified as attached to {instance_id}. Retrying... (Attempt {attempt + 1}/{max_attempts})")
            time.sleep(delay)
        logging.error(f"Timeout verifying attachment of ENI {eni_id} to instance {instance_id}.")
        return False


    def get_primary_eni_info(self, instance_id):
        """Get the ENI ID and Attachment ID of the primary network interface (eth0)."""
        try:
            details = self.get_instance_details(instance_id)
            if details:
                for ni in details.get('NetworkInterfaces', []):
                    if ni.get('Attachment', {}).get('DeviceIndex') == 0:
                        eni_id = ni['NetworkInterfaceId']
                        attachment_id = ni['Attachment']['AttachmentId']
                        logging.info(f"Found primary ENI {eni_id} with attachment {attachment_id} for instance {instance_id}.")
                        return eni_id, attachment_id
            logging.warning(f"Could not find primary ENI (device index 0) for instance {instance_id}.")
            return None, None
        except Exception as e:
            logging.error(f"Error getting primary ENI info for instance {instance_id}: {e}")
            return None, None

    def modify_eni_security_groups(self, network_interface_id, security_group_ids):
        """Modify the security groups associated with an ENI."""
        try:
            logging.info(f"Modifying security groups for ENI {network_interface_id} to {security_group_ids}...")
            self.ec2_client.modify_network_interface_attribute(
                NetworkInterfaceId=network_interface_id,
                Groups=security_group_ids
            )
            logging.info(f"Successfully modified security groups for ENI {network_interface_id}.")
            return True
        except ClientError as e:
            logging.error(f"Failed to modify security groups for ENI {network_interface_id}: {e}")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred modifying ENI security groups: {e}")
            return False

    def create_tags(self, resource_ids, tags):
        """Create tags for AWS resources."""
        # Ensure resource_ids is a list
        if isinstance(resource_ids, str):
            resource_ids = [resource_ids]

        if not tags:
            logging.info("No tags provided to create.")
            return True
        # Filter out tags with empty values, as AWS API might reject them
        valid_tags = [tag for tag in tags if tag.get('Value') is not None]
        if not valid_tags:
             logging.info("No valid tags (with non-null values) provided.")
             return True

        try:
            logging.info(f"Creating tags for resource(s) {resource_ids}: {valid_tags}")
            self.ec2_client.create_tags(Resources=resource_ids, Tags=valid_tags)
            logging.info(f"Tags created successfully for {resource_ids}.")
            return True
        except ClientError as e:
            logging.error(f"Failed to create tags for {resource_ids}: {e}")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred during tag creation: {e}")
            return False

    def delete_volume(self, volume_id: str) -> None:
        """Delete a volume."""
        try:
            logger.info(f"Deleting volume {volume_id}...")
            self.ec2_client.delete_volume(VolumeId=volume_id)
            logger.info(f"Volume {volume_id} deletion initiated.")
            # Optionally wait for deletion if needed
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidVolume.NotFound':
                 logger.warning(f"Volume {volume_id} not found, cannot delete.")
                 return # Treat as success if already gone
            logger.error(f"Error deleting volume {volume_id}: {str(e)}")
            raise

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot."""
        try:
            logger.info(f"Deleting snapshot {snapshot_id}...")
            self.ec2_client.delete_snapshot(SnapshotId=snapshot_id)
            logger.info(f"Snapshot {snapshot_id} deletion initiated.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidSnapshot.NotFound':
                 logger.warning(f"Snapshot {snapshot_id} not found, cannot delete.")
                 return # Treat as success if already gone
            logger.error(f"Error deleting snapshot {snapshot_id}: {str(e)}")
            raise

    def attach_volume(self, volume_id: str, instance_id: str, device: str) -> None:
        """Attach a volume to an instance.
        
        Args:
            volume_id: The ID of the volume to attach
            instance_id: The ID of the instance
            device: The device name to attach to
        """
        try:
            # First check if the device is already in use
            instance = self.get_instance_by_id(instance_id)
            used_devices = set()
            for block_device in instance.get('BlockDeviceMappings', []):
                if 'Ebs' in block_device:
                    used_devices.add(block_device['DeviceName'])
            
            if device in used_devices:
                logger.warning(f"Device {device} is already in use. Looking for alternative device...")
                # Try to find an available device
                for i in range(1, 16):
                    alt_device = f"/dev/sd{chr(ord('a') + i)}"
                    if alt_device not in used_devices:
                        device = alt_device
                        logger.info(f"Using alternative device {device}")
                        break
                else:
                    raise ValueError(f"No available device found for volume {volume_id}")

            logger.info(f"Attaching volume {volume_id} to instance {instance_id} at {device}")
            self.ec2_client.attach_volume(
                VolumeId=volume_id,
                InstanceId=instance_id,
                Device=device
            )
        except Exception as e:
            logger.error(f"Error attaching volume {volume_id}: {str(e)}")
            raise

    def detach_volume(self, volume_id: str) -> None:
        """Detach a volume from an instance."""
        try:
            logger.info(f"Detaching volume {volume_id}...")
            self.ec2_client.detach_volume(VolumeId=volume_id)
            logger.info(f"Volume {volume_id} detachment initiated.")
            # Note: Waiting for detachment is handled separately
        except ClientError as e:
            # Handle case where volume is not attached
            if e.response['Error']['Code'] == 'IncorrectState' and 'not attached' in e.response['Error']['Message']:
                 logger.warning(f"Volume {volume_id} is already detached.")
                 return # Treat as success
            elif e.response['Error']['Code'] == 'InvalidVolume.NotFound':
                 logger.warning(f"Volume {volume_id} not found, cannot detach.")
                 return # Treat as success if gone
            logger.error(f"Error detaching volume {volume_id}: {str(e)}")
            raise

    def wait_for_volume_available(self, volume_id: str, timeout: int = 300) -> bool:
        """Wait for a volume to become available.
        
        Args:
            volume_id: The ID of the volume to check
            timeout: Maximum time to wait in seconds
            
        Returns:
            bool: True if volume is available, False if timeout occurred
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                volume = response['Volumes'][0]
                if volume['State'] == 'available':
                    logger.info(f"Volume {volume_id} is now available")
                    return True
                elif volume['State'] == 'error':
                    logger.error(f"Volume {volume_id} is in error state")
                    return False
                time.sleep(5)  # Wait 5 seconds before checking again
            except Exception as e:
                logger.error(f"Error checking volume {volume_id} state: {str(e)}")
                time.sleep(5)
        logger.error(f"Timeout waiting for volume {volume_id} to become available")
        return False

    def wait_for_volume_detached(self, volume_id: str, timeout: int = 300) -> bool:
        """Wait for a volume to be detached.
        
        Args:
            volume_id: The ID of the volume to check
            timeout: Maximum time to wait in seconds
            
        Returns:
            bool: True if volume is detached, False if timeout occurred
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                volume = response['Volumes'][0]
                if volume['State'] == 'available' and not volume.get('Attachments'):
                    logger.info(f"Volume {volume_id} is now detached")
                    return True
                elif volume['State'] == 'error':
                    logger.error(f"Volume {volume_id} is in error state")
                    return False
                time.sleep(5)  # Wait 5 seconds before checking again
            except Exception as e:
                logger.error(f"Error checking volume {volume_id} state: {str(e)}")
                time.sleep(5)
        logger.error(f"Timeout waiting for volume {volume_id} to be detached")
        return False

    def wait_for_volume_attached(self, volume_id: str, instance_id: str, device: str, timeout: int = 300) -> bool:
        """Wait for a volume to be attached to an instance.
        
        Args:
            volume_id: The ID of the volume to check
            instance_id: The ID of the instance
            device: The device name
            timeout: Maximum time to wait in seconds
            
        Returns:
            bool: True if volume is attached, False if timeout occurred
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                volume = response['Volumes'][0]
                if volume['State'] == 'in-use' and volume.get('Attachments'):
                    attachment = volume['Attachments'][0]
                    if (attachment['InstanceId'] == instance_id and 
                        attachment['Device'] == device and 
                        attachment['State'] == 'attached'):
                        logger.info(f"Volume {volume_id} is now attached to {device}")
                        return True
                elif volume['State'] == 'error':
                    logger.error(f"Volume {volume_id} is in error state")
                    return False
                time.sleep(5)  # Wait 5 seconds before checking again
            except Exception as e:
                logger.error(f"Error checking volume {volume_id} state: {str(e)}")
                time.sleep(5)
        logger.error(f"Timeout waiting for volume {volume_id} to be attached")
        return False

    def get_instance_name(self, instance_id):
        """Helper to get the 'Name' tag of an instance."""
        try:
            details = self.get_instance_details(instance_id)
            if details and 'Tags' in details:
                for tag in details['Tags']:
                    if tag['Key'] == 'Name':
                        return tag['Value']
        except ValueError: # Handle instance not found from get_instance_details
             logger.warning(f"Cannot get name for non-existent instance {instance_id}.")
        except Exception as e:
             logger.error(f"Error getting name tag for instance {instance_id}: {e}")

        return instance_id # Fallback to ID if no Name tag or error

    def get_instance_by_id(self, instance_id: str) -> Dict:
        """Get instance details by ID (alias for get_instance_details)."""
        return self.get_instance_details(instance_id)

    def get_instance_by_name(self, instance_name: str) -> Optional[Dict]:
        """Get instance details by Name tag."""
        try:
            filters = [
                {'Name': 'tag:Name', 'Values': [instance_name]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'shutting-down', 'stopped', 'stopping']}
            ]
            response = self.ec2_client.describe_instances(Filters=filters)
            instances = []
            for reservation in response['Reservations']:
                instances.extend(reservation['Instances'])

            if not instances:
                logger.warning(f"No active or stopped instance found with Name tag: {instance_name}")
                return None
            if len(instances) > 1:
                # Return the most recently launched instance if multiple match
                logger.warning(f"Multiple instances found with Name tag: {instance_name}. Returning the most recently launched.")
                instances.sort(key=lambda x: x['LaunchTime'], reverse=True)

            return instances[0]
        except ClientError as e:
            logger.error(f"Error getting instance by name '{instance_name}': {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting instance by name '{instance_name}': {str(e)}")
            raise

    def get_instance_volumes(self, identifier: str, is_ami: bool = False) -> List[Dict]:
        """Get volumes associated with an instance or AMI."""
        try:
            if is_ami:
                logger.debug(f"Getting volume info from AMI {identifier}")
                response = self.ec2_client.describe_images(ImageIds=[identifier])
                if not response['Images']:
                    raise ValueError(f"AMI {identifier} not found.")
                image = response['Images'][0]
                volumes = []
                for bdm in image.get('BlockDeviceMappings', []):
                    if 'Ebs' in bdm and 'SnapshotId' in bdm['Ebs']:
                        # AMI block device mapping contains snapshot ID
                        snapshot_id = bdm['Ebs']['SnapshotId']
                        # Fetch snapshot details to get size, type etc.
                        try:
                            snap_response = self.ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
                            if snap_response['Snapshots']:
                                snapshot = snap_response['Snapshots'][0]
                                # Get volume type from the AMI's block device mapping
                                volume_type = bdm['Ebs'].get('VolumeType', 'gp3')  # Default to gp3 if not specified
                                volumes.append({
                                    'Device': bdm['DeviceName'],
                                    'VolumeId': snapshot_id,  # Store snapshot ID here for volume restore logic
                                    'Size': snapshot['VolumeSize'],
                                    'VolumeType': volume_type,  # Use the volume type from the AMI's block device mapping
                                    'DeleteOnTermination': bdm['Ebs'].get('DeleteOnTermination', True)
                                })
                            else:
                                logger.warning(f"Snapshot {snapshot_id} for AMI {identifier} not found.")
                        except ClientError as snap_e:
                            logger.warning(f"Could not describe snapshot {snapshot_id} for AMI {identifier}: {snap_e}")
                    elif 'DeviceName' in bdm:
                        logger.debug(f"Found non-EBS device {bdm['DeviceName']} in AMI {identifier}")

                logger.info(f"Found {len(volumes)} EBS volumes (snapshots) in AMI {identifier}")
                return volumes
            else:  # is instance
                instance_id = identifier
                logger.debug(f"Getting volume info for instance {instance_id}")
                instance = self.get_instance_details(instance_id)
                volumes = []
                for bdm in instance.get('BlockDeviceMappings', []):
                    if 'Ebs' in bdm and 'VolumeId' in bdm['Ebs']:
                        volume_id = bdm['Ebs']['VolumeId']
                        # Fetch volume details for more info (size, type)
                        try:
                            vol_response = self.ec2_client.describe_volumes(VolumeIds=[volume_id])
                            if vol_response['Volumes']:
                                vol_details = vol_response['Volumes'][0]
                                volumes.append({
                                    'Device': bdm['DeviceName'],
                                    'VolumeId': volume_id,
                                    'Size': vol_details.get('Size', 'N/A'),
                                    'VolumeType': vol_details.get('VolumeType', 'N/A'),
                                    'DeleteOnTermination': bdm['Ebs'].get('DeleteOnTermination', False)
                                })
                            else:
                                logger.warning(f"Volume {volume_id} not found for instance {instance_id}")
                        except ClientError as vol_e:
                            logger.warning(f"Could not describe volume {volume_id} for instance {instance_id}: {vol_e}")
                            # Append basic info anyway if volume ID exists
                            volumes.append({
                                'Device': bdm['DeviceName'],
                                'VolumeId': volume_id,
                                'Size': 'N/A',
                                'VolumeType': 'N/A',
                                'DeleteOnTermination': bdm['Ebs'].get('DeleteOnTermination', False)
                            })
                    elif 'DeviceName' in bdm:
                        logger.debug(f"Found non-EBS device {bdm['DeviceName']} on instance {instance_id}")

                logger.info(f"Found {len(volumes)} EBS volumes attached to instance {instance_id}")
                return volumes
        except ClientError as e:
            logger.error(f"Error getting volumes for {'AMI' if is_ami else 'instance'} {identifier}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting volumes for {'AMI' if is_ami else 'instance'} {identifier}: {str(e)}")
            raise

    def create_volume_snapshot(self, volume_id: str, description: str) -> str:
        """Create a snapshot of a volume and wait for it to start."""
        try:
            instance_id = None
            instance_name = "UnknownInstance"
            try:
                 vol_details = self.get_volume_details(volume_id)
                 if vol_details.get('Attachments'):
                     instance_id = vol_details['Attachments'][0].get('InstanceId')
                     if instance_id:
                         instance_name = self.get_instance_name(instance_id)
            except Exception as e:
                 logger.warning(f"Could not get instance details for volume {volume_id}: {e}")


            snapshot_name = f"Snapshot_{instance_name}_{volume_id}_{time.strftime('%Y%m%d-%H%M%S')}"
            logger.info(f"Creating snapshot for volume {volume_id} with description: '{description}' and Name: '{snapshot_name}'")

            tag_spec = [{
                    'ResourceType': 'snapshot',
                    'Tags': [
                        {'Key': 'Name', 'Value': snapshot_name},
                        {'Key': 'Description', 'Value': description}
                    ]
            }]
            if instance_id:
                 tag_spec[0]['Tags'].append({'Key': 'SourceInstanceId', 'Value': instance_id})
            if instance_name != "UnknownInstance":
                 tag_spec[0]['Tags'].append({'Key': 'SourceInstanceName', 'Value': instance_name})


            response = self.ec2_client.create_snapshot(
                VolumeId=volume_id,
                Description=description,
                TagSpecifications=tag_spec
            )
            snapshot_id = response['SnapshotId']
            logger.info(f"Snapshot creation initiated: {snapshot_id}")
            # Optionally wait for snapshot to be 'pending' or 'available' if needed immediately
            # self.wait_for_snapshot_completion(snapshot_id) # Or a shorter wait just to confirm creation
            return snapshot_id
        except ClientError as e:
            logger.error(f"Error creating snapshot for volume {volume_id}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating snapshot for volume {volume_id}: {str(e)}")
            raise

    def wait_for_instance_state(self, instance_id: str, state: str, delay: int = 15, max_attempts: int = 40) -> bool:
        """Wait for an instance to reach a specific state using a waiter."""
        waiter_name = f'instance_{state}' # e.g., instance_stopped, instance_terminated, instance_running
        try:
            waiter = self.ec2_client.get_waiter(waiter_name)
            logger.info(f"Waiting for instance {instance_id} to reach '{state}' state...")
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={'Delay': delay, 'MaxAttempts': max_attempts}
            )
            logger.info(f"Instance {instance_id} reached state '{state}'.")
            return True
        except WaiterError as e:
            logger.error(f"Waiter failed or timed out waiting for instance {instance_id} to reach state '{state}': {e}")
            # Check current state if waiter failed
            try:
                details = self.get_instance_details(instance_id)
                current_state = details['State']['Name']
                logger.info(f"Instance {instance_id} current state is '{current_state}' after waiter timeout for '{state}'.")
                if current_state == state:
                    logger.warning(f"Instance {instance_id} is already in the desired state '{state}' despite waiter error.")
                    return True
            except ValueError: # Instance not found
                 if state == 'terminated':
                      logger.info(f"Instance {instance_id} not found, considered terminated.")
                      return True
                 else:
                      logger.error(f"Instance {instance_id} not found while waiting for state '{state}'.")
            except Exception as check_e:
                 logger.error(f"Could not check instance state after waiter error: {check_e}")
            return False
        except ClientError as e:
             # Handle cases like instance not found if waiting for terminated
             if state == 'terminated' and e.response['Error']['Code'] == 'InvalidInstanceID.NotFound':
                 logger.info(f"Instance {instance_id} not found, considered terminated.")
                 return True
             logger.error(f"API error while waiting for instance {instance_id} to reach state '{state}': {e}")
             return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while waiting for instance {instance_id} to reach state '{state}': {e}")
            return False

    def modify_network_interface_attribute(self, network_interface_id: str, attachment_id: str, delete_on_termination: bool) -> bool:
        """Modify the DeleteOnTermination attribute for a network interface attachment."""
        try:
            logger.info(f"Setting DeleteOnTermination={delete_on_termination} for ENI {network_interface_id} attachment {attachment_id}")
            self.ec2_client.modify_network_interface_attribute(
                NetworkInterfaceId=network_interface_id,
                Attachment={
                    'AttachmentId': attachment_id,
                    'DeleteOnTermination': delete_on_termination
                }
            )
            logger.info(f"Successfully modified DeleteOnTermination for ENI {network_interface_id}.")
            return True
        except ClientError as e:
            logger.error(f"Failed to modify DeleteOnTermination for ENI {network_interface_id}: {e}")
            # Handle specific errors like invalid attachment ID if necessary
            if 'InvalidAttachmentID.NotFound' in str(e):
                 logger.warning(f"Attachment ID {attachment_id} not found for ENI {network_interface_id}. Cannot modify attribute.")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred modifying ENI attribute: {e}")
            return False

    def create_instance_with_config(self, launch_params: Dict) -> str:
        """Create a new instance using provided launch parameters."""
        try:
            logger.info(f"Creating new instance with parameters: {launch_params}")
            response = self.ec2_client.run_instances(**launch_params)
            if not response or 'Instances' not in response or not response['Instances']:
                raise Exception("run_instances call did not return expected instance data.")

            new_instance_id = response['Instances'][0]['InstanceId']
            logger.info(f"Instance creation initiated: {new_instance_id}")

            # Wait for the instance to be running before returning
            if not self.wait_for_instance_running(new_instance_id):
                # Attempt to terminate the failed instance
                logger.error(f"Instance {new_instance_id} did not reach running state. Attempting termination.")
                try:
                    self.terminate_instance(new_instance_id)
                except Exception as term_e:
                     logger.error(f"Failed to terminate instance {new_instance_id} after launch failure: {term_e}")
                raise Exception(f"Instance {new_instance_id} did not reach running state.")

            logger.info(f"Instance {new_instance_id} created and is running.")
            return new_instance_id
        except ClientError as e:
            logger.error(f"Failed to create instance with config: {e}")
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during instance creation: {e}")
            raise

    def wait_for_instance_availability(self, instance_id: str, timeout: int = 300) -> bool:
        """Wait for an instance to be available (running and passed status checks)."""
        logger.info(f"Waiting for instance {instance_id} to become fully available (running + status checks)...")
        start_wait = time.time()
        # First wait for running state
        if not self.wait_for_instance_running(instance_id, max_attempts=timeout // 15):
            logger.error(f"Instance {instance_id} did not reach running state within timeout.")
            return False
        time_spent_running = time.time() - start_wait
        remaining_timeout = timeout - time_spent_running
        if remaining_timeout <= 0:
             logger.warning(f"Instance {instance_id} reached running state, but no time left for status checks.")
             return True # Consider running as available enough

        # Then wait for status checks to pass
        try:
            waiter = self.ec2_client.get_waiter('instance_status_ok')
            max_attempts_status = max(1, int(remaining_timeout // 15)) # Ensure at least 1 attempt
            logger.info(f"Waiting for instance {instance_id} status checks to pass (max {max_attempts_status} attempts)...")
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={'Delay': 15, 'MaxAttempts': max_attempts_status }
            )
            logger.info(f"Instance {instance_id} passed status checks.")
            return True
        except WaiterError as e:
            logger.error(f"Waiter failed or timed out waiting for instance {instance_id} status checks: {e}")
            # Instance might be running but having issues. Consider it 'available' in a basic sense?
            # For restore purposes, running might be sufficient. Let's return True but log warning.
            logger.warning(f"Instance {instance_id} is running but did not pass status checks within timeout.")
            return True # Treat as available for restore continuation, but log warning.
        except ClientError as e:
             logger.error(f"API error while waiting for instance {instance_id} status checks: {e}")
             return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while waiting for instance {instance_id} status checks: {e}")
            return False

    def force_detach_volume(self, volume_id: str) -> bool:
        """Force detach a volume."""
        try:
            logger.warning(f"Attempting to force detach volume {volume_id}")
            self.ec2_client.detach_volume(VolumeId=volume_id, Force=True)
            logger.info(f"Force detachment initiated for volume {volume_id}")
            # Wait for detachment after force detach
            return self.wait_for_volume_detached(volume_id)
        except ClientError as e:
            logger.error(f"Error force detaching volume {volume_id}: {str(e)}")
            # Handle cases where it might already be detached
            if 'IncorrectState' in str(e) and ('detaching' in str(e) or 'available' in str(e)):
                 logger.info(f"Volume {volume_id} is already detaching or available.")
                 return self.wait_for_volume_detached(volume_id) # Wait anyway if detaching, return true if available
            elif 'InvalidVolume.NotFound' in str(e):
                 logger.warning(f"Volume {volume_id} not found during force detach attempt.")
                 return True # Already gone
            return False
        except Exception as e:
            logger.error(f"Unexpected error during force detach for volume {volume_id}: {e}")
            return False

    def get_instances(self) -> List[Dict]:
        """Get all non-terminated instances in the configured region."""
        try:
            paginator = self.ec2_client.get_paginator('describe_instances')
            instances = []
            # Filter for non-terminated instances
            page_iterator = paginator.paginate(
                Filters=[{'Name': 'instance-state-name',
                          'Values': ['pending', 'running', 'shutting-down', 'stopped', 'stopping']}]
            )
            for page in page_iterator:
                for reservation in page['Reservations']:
                    instances.extend(reservation['Instances'])
            logger.info(f"Retrieved {len(instances)} non-terminated instances.")
            return instances
        except ClientError as e:
            logger.error(f"Error retrieving instances: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error retrieving instances: {str(e)}")
            raise