"""
Unit tests for Media Lister Lambda function.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import sys
import os

# Add lambda function and layer to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda_functions/media_lister'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda_layer/python'))

import handler


@pytest.fixture
def valid_credentials():
    """Fixture for valid credentials."""
    return {
        'gp-access-token': 'eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.test_token',
        'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234',
        'user-agent': 'Mozilla/5.0 (Test)'
    }


@pytest.fixture
def sample_videos():
    """Fixture for sample video metadata."""
    return [
        {
            'media_id': 'video-123',
            'filename': 'GH010001.MP4',
            'download_url': 'https://example.com/video1.mp4',
            'file_size': 1073741824,  # 1 GB
            'upload_date': '2025-12-01T00:00:00Z',
            'duration': 120,
            'media_type': 'video',
            'resolution': '4K'
        },
        {
            'media_id': 'video-456',
            'filename': 'GH020002.MP4',
            'download_url': 'https://example.com/video2.mp4',
            'file_size': 2147483648,  # 2 GB
            'upload_date': '2025-12-01T01:00:00Z',
            'duration': 240,
            'media_type': 'video',
            'resolution': '4K'
        },
        {
            'media_id': 'video-789',
            'filename': 'GH030003.MP4',
            'download_url': 'https://example.com/video3.mp4',
            'file_size': 1610612736,  # 1.5 GB
            'upload_date': '2025-12-01T02:00:00Z',
            'duration': 180,
            'media_type': 'video',
            'resolution': '4K'
        }
    ]


@pytest.fixture
def lambda_context():
    """Fixture for Lambda context."""
    context = Mock()
    context.function_name = 'media-lister'
    context.function_version = '1'
    context.invoked_function_arn = 'arn:aws:lambda:us-east-1:123456789012:function:media-lister'
    context.memory_limit_in_mb = 512
    context.aws_request_id = 'test-request-id'
    return context


@pytest.fixture
def mock_video_metadata():
    """Fixture for mock VideoMetadata objects."""
    class VideoMetadata:
        def __init__(self, media_id, filename, download_url, file_size, upload_date, duration):
            self.media_id = media_id
            self.filename = filename
            self.download_url = download_url
            self.file_size = file_size
            self.upload_date = upload_date
            self.duration = duration
            self.media_type = 'video'
            self.resolution = '4K'
    
    return [
        VideoMetadata('video-123', 'GH010001.MP4', 'https://example.com/v1.mp4', 1073741824, '2025-12-01T00:00:00Z', 120),
        VideoMetadata('video-456', 'GH020002.MP4', 'https://example.com/v2.mp4', 2147483648, '2025-12-01T01:00:00Z', 240),
        VideoMetadata('video-789', 'GH030003.MP4', 'https://example.com/v3.mp4', 1610612736, '2025-12-01T02:00:00Z', 180)
    ]


class TestMediaListerHandler:
    """Tests for Media Lister Lambda handler."""
    
    @patch('handler.secrets_client')
    @patch('handler.GoProProvider')
    @patch('handler.batch_get_sync_status')
    @patch('handler.metrics_publisher')
    def test_handler_success_all_new_videos(
        self, mock_metrics, mock_batch_get, mock_provider_class, 
        mock_secrets, valid_credentials, mock_video_metadata, lambda_context
    ):
        """Test successful listing with all new videos."""
        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.list_media.return_value = mock_video_metadata
        mock_provider_class.return_value = mock_provider
        
        # Mock DynamoDB - no videos synced yet
        mock_batch_get.return_value = {}
        
        # Call handler
        event = {'correlation_id': 'test-correlation-id'}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 200
        assert result['new_count'] == 3
        assert result['total_found'] == 3
        assert result['already_synced'] == 0
        assert len(result['new_videos']) == 3
        
        # Verify provider was called correctly
        mock_provider.list_media.assert_called_once()
        call_kwargs = mock_provider.list_media.call_args[1]
        assert 'cookies' in call_kwargs
        assert 'user_agent' in call_kwargs
        
    @patch('handler.secrets_client')
    @patch('handler.GoProProvider')
    @patch('handler.batch_get_sync_status')
    @patch('handler.metrics_publisher')
    def test_handler_success_some_already_synced(
        self, mock_metrics, mock_batch_get, mock_provider_class,
        mock_secrets, valid_credentials, mock_video_metadata, lambda_context
    ):
        """Test listing with some videos already synced."""
        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.list_media.return_value = mock_video_metadata
        mock_provider_class.return_value = mock_provider
        
        # Mock DynamoDB - video-123 already synced
        mock_batch_get.return_value = {
            'video-123': 'COMPLETED'
        }
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 200
        assert result['new_count'] == 2  # Only video-456 and video-789
        assert result['total_found'] == 3
        assert result['already_synced'] == 1
        assert len(result['new_videos']) == 2
        
        # Verify correct videos are in result
        new_video_ids = [v['media_id'] for v in result['new_videos']]
        assert 'video-456' in new_video_ids
        assert 'video-789' in new_video_ids
        assert 'video-123' not in new_video_ids
        
    @patch('handler.secrets_client')
    @patch('handler.GoProProvider')
    @patch('handler.batch_get_sync_status')
    @patch('handler.metrics_publisher')
    def test_handler_success_all_already_synced(
        self, mock_metrics, mock_batch_get, mock_provider_class,
        mock_secrets, valid_credentials, mock_video_metadata, lambda_context
    ):
        """Test listing when all videos already synced."""
        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.list_media.return_value = mock_video_metadata
        mock_provider_class.return_value = mock_provider
        
        # Mock DynamoDB - all videos already synced
        mock_batch_get.return_value = {
            'video-123': 'COMPLETED',
            'video-456': 'COMPLETED',
            'video-789': 'COMPLETED'
        }
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 200
        assert result['new_count'] == 0
        assert result['total_found'] == 3
        assert result['already_synced'] == 3
        assert len(result['new_videos']) == 0
        
    @patch('handler.secrets_client')
    @patch('handler.GoProProvider')
    @patch('handler.batch_get_sync_status')
    @patch('handler.metrics_publisher')
    def test_handler_includes_in_progress_videos(
        self, mock_metrics, mock_batch_get, mock_provider_class,
        mock_secrets, valid_credentials, mock_video_metadata, lambda_context
    ):
        """Test that videos with IN_PROGRESS status are included."""
        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.list_media.return_value = mock_video_metadata
        mock_provider_class.return_value = mock_provider
        
        # Mock DynamoDB - video-123 in progress, video-456 failed
        mock_batch_get.return_value = {
            'video-123': 'IN_PROGRESS',
            'video-456': 'FAILED'
        }
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify response - all 3 should be included (IN_PROGRESS, FAILED, and None)
        assert result['statusCode'] == 200
        assert result['new_count'] == 3
        assert len(result['new_videos']) == 3
        
    @patch('handler.secrets_client')
    def test_handler_missing_secret(self, mock_secrets, lambda_context):
        """Test handling when secret doesn't exist."""
        # Create proper exception class
        class ResourceNotFoundException(Exception):
            pass
        
        mock_secrets.exceptions.ResourceNotFoundException = ResourceNotFoundException
        mock_secrets.get_secret_value.side_effect = ResourceNotFoundException('Secret not found')
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify error response
        assert result['statusCode'] == 500
        assert 'error' in result
        
    @patch('handler.secrets_client')
    @patch('handler.GoProProvider')
    @patch('handler.metrics_publisher')
    def test_handler_provider_error(
        self, mock_metrics, mock_provider_class, mock_secrets,
        valid_credentials, lambda_context
    ):
        """Test handling of provider errors."""
        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock provider error
        mock_provider = Mock()
        mock_provider.list_media.side_effect = Exception('API error')
        mock_provider_class.return_value = mock_provider
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify error response
        assert result['statusCode'] == 500
        assert 'error' in result


class TestFilterNewVideos:
    """Tests for filter_new_videos function."""
    
    @patch('handler.batch_get_sync_status')
    @patch('handler.dynamodb')
    def test_filter_all_new(self, mock_dynamodb, mock_batch_get, sample_videos):
        """Test filtering when all videos are new."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        mock_dynamodb.Table.return_value = mock_table
        
        # No videos synced yet
        mock_batch_get.return_value = {}
        
        result = handler.filter_new_videos(sample_videos)
        
        assert len(result) == 3
        assert all(v in result for v in sample_videos)
        
    @patch('handler.batch_get_sync_status')
    @patch('handler.dynamodb')
    def test_filter_some_synced(self, mock_dynamodb, mock_batch_get, sample_videos):
        """Test filtering when some videos already synced."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        mock_dynamodb.Table.return_value = mock_table
        
        # One video completed
        mock_batch_get.return_value = {
            'video-123': 'COMPLETED'
        }
        
        result = handler.filter_new_videos(sample_videos)
        
        assert len(result) == 2
        video_ids = [v['media_id'] for v in result]
        assert 'video-456' in video_ids
        assert 'video-789' in video_ids
        assert 'video-123' not in video_ids
        
    @patch('handler.batch_get_sync_status')
    @patch('handler.dynamodb')
    def test_filter_all_synced(self, mock_dynamodb, mock_batch_get, sample_videos):
        """Test filtering when all videos already synced."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        mock_dynamodb.Table.return_value = mock_table
        
        # All videos completed
        mock_batch_get.return_value = {
            'video-123': 'COMPLETED',
            'video-456': 'COMPLETED',
            'video-789': 'COMPLETED'
        }
        
        result = handler.filter_new_videos(sample_videos)
        
        assert len(result) == 0
        
    @patch('handler.batch_get_sync_status')
    @patch('handler.dynamodb')
    def test_filter_includes_non_completed_statuses(self, mock_dynamodb, mock_batch_get, sample_videos):
        """Test that non-COMPLETED statuses are included."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        mock_dynamodb.Table.return_value = mock_table
        
        # Various statuses
        mock_batch_get.return_value = {
            'video-123': 'IN_PROGRESS',
            'video-456': 'FAILED',
            'video-789': 'PENDING'
        }
        
        result = handler.filter_new_videos(sample_videos)
        
        # All should be included (none are COMPLETED)
        assert len(result) == 3
        
    def test_filter_empty_list(self):
        """Test filtering empty list."""
        result = handler.filter_new_videos([])
        
        assert result == []


class TestBatchGetSyncStatus:
    """Tests for batch_get_sync_status function."""
    
    @patch('handler.dynamodb')
    def test_batch_get_single_batch(self, mock_dynamodb):
        """Test batch get with single batch (< 100 items)."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        
        # Mock batch_get_item response
        mock_dynamodb.batch_get_item.return_value = {
            'Responses': {
                'test-table': [
                    {'media_id': 'video-1', 'status': 'COMPLETED'},
                    {'media_id': 'video-2', 'status': 'IN_PROGRESS'}
                ]
            },
            'UnprocessedKeys': {}
        }
        
        media_ids = ['video-1', 'video-2', 'video-3']
        result = handler.batch_get_sync_status(mock_table, media_ids)
        
        assert result['video-1'] == 'COMPLETED'
        assert result['video-2'] == 'IN_PROGRESS'
        assert 'video-3' not in result  # Not in DynamoDB
        
    @patch('handler.dynamodb')
    def test_batch_get_multiple_batches(self, mock_dynamodb):
        """Test batch get with multiple batches (>= 100 items)."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        
        # Create 150 media IDs
        media_ids = [f'video-{i}' for i in range(150)]
        
        # Mock responses for two batches
        responses = []
        for i in range(2):
            batch_size = 100 if i == 0 else 50
            batch_items = [
                {'media_id': f'video-{j}', 'status': 'COMPLETED'}
                for j in range(i * 100, i * 100 + batch_size)
            ]
            responses.append({
                'Responses': {'test-table': batch_items},
                'UnprocessedKeys': {}
            })
        
        mock_dynamodb.batch_get_item.side_effect = responses
        
        result = handler.batch_get_sync_status(mock_table, media_ids)
        
        # Verify all items retrieved
        assert len(result) == 150
        assert all(result[f'video-{i}'] == 'COMPLETED' for i in range(150))
        
        # Verify batch_get_item called twice
        assert mock_dynamodb.batch_get_item.call_count == 2
        
    @patch('handler.dynamodb')
    def test_batch_get_with_retries(self, mock_dynamodb):
        """Test batch get with unprocessed keys retry."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        
        # First call: some unprocessed keys
        # Second call: all processed
        mock_dynamodb.batch_get_item.side_effect = [
            {
                'Responses': {
                    'test-table': [
                        {'media_id': 'video-1', 'status': 'COMPLETED'}
                    ]
                },
                'UnprocessedKeys': {
                    'test-table': {
                        'Keys': [{'media_id': 'video-2'}]
                    }
                }
            },
            {
                'Responses': {
                    'test-table': [
                        {'media_id': 'video-2', 'status': 'COMPLETED'}
                    ]
                },
                'UnprocessedKeys': {}
            }
        ]
        
        media_ids = ['video-1', 'video-2']
        result = handler.batch_get_sync_status(mock_table, media_ids)
        
        # Verify both items retrieved
        assert len(result) == 2
        assert result['video-1'] == 'COMPLETED'
        assert result['video-2'] == 'COMPLETED'
        
        # Verify retry occurred
        assert mock_dynamodb.batch_get_item.call_count == 2
        
    @patch('handler.dynamodb')
    def test_batch_get_error_handling(self, mock_dynamodb):
        """Test batch get continues on error."""
        mock_table = Mock()
        mock_table.name = 'test-table'
        
        # Mock error
        mock_dynamodb.batch_get_item.side_effect = Exception('DynamoDB error')
        
        media_ids = ['video-1', 'video-2']
        result = handler.batch_get_sync_status(mock_table, media_ids)
        
        # Should return empty dict, not raise error
        assert result == {}


class TestValidateVideoMetadata:
    """Tests for validate_video_metadata function."""
    
    def test_validate_complete_metadata(self):
        """Test validation of complete metadata."""
        video = {
            'media_id': 'video-123',
            'filename': 'test.mp4',
            'download_url': 'https://example.com/video.mp4',
            'file_size': 1073741824
        }
        
        # Should not raise
        handler.validate_video_metadata(video)
        
    def test_validate_missing_media_id(self):
        """Test validation fails when media_id missing."""
        video = {
            'filename': 'test.mp4',
            'download_url': 'https://example.com/video.mp4',
            'file_size': 1073741824
        }
        
        from cloud_sync_common.exceptions import APIError
        
        with pytest.raises(APIError) as exc_info:
            handler.validate_video_metadata(video)
        
        assert 'media_id' in str(exc_info.value)
        assert exc_info.value.status_code == 200
        
    def test_validate_missing_download_url(self):
        """Test validation fails when download_url missing."""
        video = {
            'media_id': 'video-123',
            'filename': 'test.mp4',
            'file_size': 1073741824
        }
        
        from cloud_sync_common.exceptions import APIError
        
        with pytest.raises(APIError) as exc_info:
            handler.validate_video_metadata(video)
        
        assert 'download_url' in str(exc_info.value)
        
    def test_validate_multiple_missing_fields(self):
        """Test validation with multiple missing fields."""
        video = {
            'media_id': 'video-123'
        }
        
        from cloud_sync_common.exceptions import APIError
        
        with pytest.raises(APIError) as exc_info:
            handler.validate_video_metadata(video)
        
        error_message = str(exc_info.value)
        assert 'filename' in error_message
        assert 'download_url' in error_message
        assert 'file_size' in error_message


class TestRetrieveCredentials:
    """Tests for retrieve_credentials function."""
    
    @patch('handler.secrets_client')
    def test_retrieve_success(self, mock_secrets, valid_credentials):
        """Test successful credential retrieval."""
        mock_secrets.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        result = handler.retrieve_credentials()
        
        assert result == valid_credentials
        
    @patch('handler.secrets_client')
    def test_retrieve_not_found(self, mock_secrets):
        """Test handling when secret not found."""
        # Create proper exception class
        class ResourceNotFoundException(Exception):
            pass
        
        mock_secrets.exceptions.ResourceNotFoundException = ResourceNotFoundException
        mock_secrets.get_secret_value.side_effect = ResourceNotFoundException('Secret not found')
        
        from cloud_sync_common.exceptions import ProviderError
        
        with pytest.raises(ProviderError) as exc_info:
            handler.retrieve_credentials()
        
        assert 'Secret not found' in str(exc_info.value)


class TestPublishApiStructureAlert:
    """Tests for publish_api_structure_alert function."""
    
    @patch('handler.sns_client')
    @patch('handler.metrics_publisher')
    @patch('handler.SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789012:test-topic')
    def test_publish_alert_success(self, mock_metrics, mock_sns):
        """Test successful API structure alert."""
        handler.publish_api_structure_alert(
            'API structure changed',
            'test-correlation-id',
            '{"unexpected": "response"}'
        )
        
        # Verify SNS publish
        assert mock_sns.publish.called
        call_args = mock_sns.publish.call_args
        
        # Verify message structure
        message = json.loads(call_args[1]['Message'])
        assert message['alert_type'] == 'API_STRUCTURE_CHANGE'
        assert message['severity'] == 'MEDIUM'
        assert message['function'] == 'media-lister'
        
        # Verify metric published
        assert mock_metrics.put_metric.called
        
    @patch('handler.SNS_TOPIC_ARN', None)
    def test_publish_alert_no_topic(self):
        """Test alert when no SNS topic configured."""
        # Should not raise error
        handler.publish_api_structure_alert('Test', 'correlation-id')
        
    @patch('handler.sns_client')
    @patch('handler.metrics_publisher')
    @patch('handler.SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789012:test-topic')
    def test_publish_alert_with_long_response(self, mock_metrics, mock_sns):
        """Test alert truncates long response samples."""
        long_response = 'x' * 1000
        
        handler.publish_api_structure_alert(
            'Test',
            'correlation-id',
            long_response
        )
        
        # Verify truncation
        call_args = mock_sns.publish.call_args
        message = json.loads(call_args[1]['Message'])
        assert len(message['response_sample']) == 500
