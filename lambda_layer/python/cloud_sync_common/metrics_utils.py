"""CloudWatch metrics publishing utilities."""

import boto3
from typing import List, Dict, Any, Optional
from datetime import datetime


class MetricsPublisher:
    """Utility for publishing CloudWatch metrics."""

    def __init__(self, namespace: str = "GoProSync"):
        """Initialize metrics publisher.
        
        Args:
            namespace: CloudWatch namespace for metrics
        """
        self.namespace = namespace
        self.cloudwatch = boto3.client('cloudwatch')

    def put_metric(
        self,
        metric_name: str,
        value: float,
        unit: str = 'None',
        dimensions: Optional[Dict[str, str]] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """Publish a single metric to CloudWatch.
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: Metric unit (Count, Bytes, Seconds, etc.)
            dimensions: Optional metric dimensions
            timestamp: Optional timestamp (defaults to now)
        """
        metric_data = {
            'MetricName': metric_name,
            'Value': value,
            'Unit': unit,
            'Timestamp': timestamp or datetime.utcnow()
        }

        if dimensions:
            metric_data['Dimensions'] = [
                {'Name': k, 'Value': v} for k, v in dimensions.items()
            ]

        self.cloudwatch.put_metric_data(
            Namespace=self.namespace,
            MetricData=[metric_data]
        )

    def put_metrics(self, metrics: List[Dict[str, Any]]) -> None:
        """Publish multiple metrics to CloudWatch.
        
        Args:
            metrics: List of metric dictionaries with keys:
                - metric_name: str
                - value: float
                - unit: str (optional)
                - dimensions: dict (optional)
                - timestamp: datetime (optional)
        """
        metric_data = []
        
        for metric in metrics:
            data = {
                'MetricName': metric['metric_name'],
                'Value': metric['value'],
                'Unit': metric.get('unit', 'None'),
                'Timestamp': metric.get('timestamp', datetime.utcnow())
            }
            
            if 'dimensions' in metric:
                data['Dimensions'] = [
                    {'Name': k, 'Value': v}
                    for k, v in metric['dimensions'].items()
                ]
            
            metric_data.append(data)

        # CloudWatch allows max 20 metrics per request
        for i in range(0, len(metric_data), 20):
            batch = metric_data[i:i + 20]
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=batch
            )

    def record_video_synced(
        self,
        provider: str,
        environment: str,
        bytes_transferred: int,
        duration_seconds: float
    ) -> None:
        """Record successful video sync metrics.
        
        Args:
            provider: Cloud provider name
            environment: Environment name (dev, staging, prod)
            bytes_transferred: Number of bytes transferred
            duration_seconds: Transfer duration in seconds
        """
        dimensions = {
            'Provider': provider,
            'Environment': environment
        }

        # Calculate throughput in Mbps
        throughput_mbps = (bytes_transferred * 8) / (duration_seconds * 1_000_000)

        metrics = [
            {
                'metric_name': 'VideosSynced',
                'value': 1,
                'unit': 'Count',
                'dimensions': dimensions
            },
            {
                'metric_name': 'BytesTransferred',
                'value': bytes_transferred,
                'unit': 'Bytes',
                'dimensions': dimensions
            },
            {
                'metric_name': 'TransferDuration',
                'value': duration_seconds,
                'unit': 'Seconds',
                'dimensions': dimensions
            },
            {
                'metric_name': 'TransferThroughput',
                'value': throughput_mbps,
                'unit': 'None',
                'dimensions': dimensions
            }
        ]

        self.put_metrics(metrics)

    def record_sync_failure(
        self,
        provider: str,
        environment: str,
        error_type: str
    ) -> None:
        """Record sync failure metric.
        
        Args:
            provider: Cloud provider name
            environment: Environment name
            error_type: Type of error (NetworkError, AuthenticationError, etc.)
        """
        self.put_metric(
            metric_name='SyncFailures',
            value=1,
            unit='Count',
            dimensions={
                'Provider': provider,
                'Environment': environment,
                'ErrorType': error_type
            }
        )

    def record_authentication(
        self,
        provider: str,
        environment: str,
        success: bool
    ) -> None:
        """Record authentication attempt metric.
        
        Args:
            provider: Cloud provider name
            environment: Environment name
            success: Whether authentication succeeded
        """
        metric_name = 'AuthenticationSuccess' if success else 'AuthenticationFailure'
        
        self.put_metric(
            metric_name=metric_name,
            value=1,
            unit='Count',
            dimensions={
                'Provider': provider,
                'Environment': environment
            }
        )
