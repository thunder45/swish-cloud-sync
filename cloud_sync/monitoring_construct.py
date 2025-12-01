"""
CloudWatch Monitoring Construct for Cloud Sync Application.

This construct creates:
- CloudWatch Dashboard with operational metrics
- CloudWatch Alarms for critical failures
- CloudWatch Logs Insights saved queries
"""

from aws_cdk import (
    Duration,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_lambda as lambda_,
    aws_stepfunctions as sfn,
    aws_sqs as sqs,
    aws_logs as logs,
)
from constructs import Construct
from typing import Dict, List


class MonitoringConstruct(Construct):
    """Creates CloudWatch monitoring resources for the Cloud Sync Application."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sns_topic: sns.ITopic,
        lambda_functions: Dict[str, lambda_.IFunction],
        state_machine: sfn.IStateMachine,
        dlqs: Dict[str, sqs.IQueue],
        environment: str = "dev",
        **kwargs
    ) -> None:
        """
        Initialize the monitoring construct.

        Args:
            scope: CDK scope
            construct_id: Construct identifier
            sns_topic: SNS topic for alarm notifications
            lambda_functions: Dictionary of Lambda functions to monitor
            state_machine: Step Functions state machine to monitor
            dlqs: Dictionary of Dead Letter Queues to monitor
            environment: Deployment environment (dev, staging, prod)
        """
        super().__init__(scope, construct_id, **kwargs)

        self.sns_topic = sns_topic
        self.lambda_functions = lambda_functions
        self.state_machine = state_machine
        self.dlqs = dlqs
        self.environment = environment
        self.namespace = "GoProSync"

        # Create CloudWatch alarms
        self._create_alarms()

        # Create CloudWatch dashboard
        self._create_dashboard()

        # Create CloudWatch Logs Insights queries
        self._create_logs_insights_queries()

        # Configure log retention
        self._configure_log_retention()

    def _create_alarms(self) -> None:
        """Create CloudWatch alarms for monitoring critical failures."""
        
        # Alarm 1: High Failure Rate (custom metric)
        high_failure_alarm = cloudwatch.Alarm(
            self,
            "HighFailureRateAlarm",
            alarm_name=f"{self.environment}-GoPro-Sync-HighFailureRate",
            alarm_description="More than 3 sync failures detected in 5 minutes",
            metric=cloudwatch.Metric(
                namespace=self.namespace,
                metric_name="SyncFailures",
                dimensions_map={
                    "Provider": "gopro",
                    "Environment": self.environment
                },
                statistic="Sum",
                period=Duration.minutes(5)
            ),
            threshold=3,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        high_failure_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 2: Authentication Failure (custom metric)
        auth_failure_alarm = cloudwatch.Alarm(
            self,
            "AuthFailureAlarm",
            alarm_name=f"{self.environment}-GoPro-Auth-Failure",
            alarm_description="Authentication failure detected",
            metric=cloudwatch.Metric(
                namespace=self.namespace,
                metric_name="AuthenticationFailure",
                dimensions_map={
                    "Provider": "gopro",
                    "Environment": self.environment
                },
                statistic="Sum",
                period=Duration.minutes(5)
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        auth_failure_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 3: Lambda Errors (for each Lambda function)
        for function_name, function in self.lambda_functions.items():
            lambda_error_alarm = cloudwatch.Alarm(
                self,
                f"{function_name}ErrorAlarm",
                alarm_name=f"{self.environment}-GoPro-Lambda-Errors-{function_name}",
                alarm_description=f"More than 5 errors in {function_name} Lambda in 5 minutes",
                metric=function.metric_errors(
                    statistic="Sum",
                    period=Duration.minutes(5)
                ),
                threshold=5,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            lambda_error_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 4: Lambda Throttles (for each Lambda function)
        for function_name, function in self.lambda_functions.items():
            lambda_throttle_alarm = cloudwatch.Alarm(
                self,
                f"{function_name}ThrottleAlarm",
                alarm_name=f"{self.environment}-GoPro-Lambda-Throttles-{function_name}",
                alarm_description=f"Lambda throttling detected for {function_name}",
                metric=function.metric_throttles(
                    statistic="Sum",
                    period=Duration.minutes(5)
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            lambda_throttle_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 5: Step Functions Failures
        sfn_failure_alarm = cloudwatch.Alarm(
            self,
            "StepFunctionFailureAlarm",
            alarm_name=f"{self.environment}-GoPro-StepFunction-Failed",
            alarm_description="Step Functions execution failed",
            metric=self.state_machine.metric_failed(
                statistic="Sum",
                period=Duration.minutes(5)
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        sfn_failure_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 6: DLQ Messages (for each DLQ)
        for dlq_name, dlq in self.dlqs.items():
            dlq_alarm = cloudwatch.Alarm(
                self,
                f"{dlq_name}DLQAlarm",
                alarm_name=f"{self.environment}-GoPro-DLQ-Messages-{dlq_name}",
                alarm_description=f"Messages detected in {dlq_name} DLQ",
                metric=dlq.metric_approximate_number_of_messages_visible(
                    statistic="Average",
                    period=Duration.minutes(5)
                ),
                threshold=0,
                evaluation_periods=2,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            dlq_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 7: Low Throughput (custom metric)
        low_throughput_alarm = cloudwatch.Alarm(
            self,
            "LowThroughputAlarm",
            alarm_name=f"{self.environment}-GoPro-Low-Throughput",
            alarm_description="Transfer throughput below 20 Mbps for 15 minutes",
            metric=cloudwatch.Metric(
                namespace=self.namespace,
                metric_name="TransferThroughput",
                dimensions_map={
                    "Provider": "gopro",
                    "Environment": self.environment
                },
                statistic="Average",
                period=Duration.minutes(15)
            ),
            threshold=20,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        low_throughput_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

        # Alarm 8: Secrets Rotation Failure
        rotation_failure_alarm = cloudwatch.Alarm(
            self,
            "SecretsRotationFailureAlarm",
            alarm_name=f"{self.environment}-GoPro-Secrets-Rotation-Failure",
            alarm_description="Secrets rotation failed",
            metric=cloudwatch.Metric(
                namespace="CloudSync/SecretsRotation",
                metric_name="RotationFailure",
                dimensions_map={
                    "Provider": "gopro"
                },
                statistic="Sum",
                period=Duration.hours(1)
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        rotation_failure_alarm.add_alarm_action(cw_actions.SnsAction(self.sns_topic))

    def _create_dashboard(self) -> None:
        """Create CloudWatch dashboard with operational metrics."""
        
        dashboard = cloudwatch.Dashboard(
            self,
            "OperationsDashboard",
            dashboard_name=f"{self.environment}-GoPro-Sync-Operations"
        )

        # Widget 1: Sync Success Rate
        sync_success_widget = cloudwatch.GraphWidget(
            title="Sync Success Rate",
            left=[
                cloudwatch.Metric(
                    namespace=self.namespace,
                    metric_name="VideosSynced",
                    dimensions_map={
                        "Provider": "gopro",
                        "Environment": self.environment
                    },
                    statistic="Sum",
                    period=Duration.hours(1),
                    label="Videos Synced"
                ),
                cloudwatch.Metric(
                    namespace=self.namespace,
                    metric_name="SyncFailures",
                    dimensions_map={
                        "Provider": "gopro",
                        "Environment": self.environment
                    },
                    statistic="Sum",
                    period=Duration.hours(1),
                    label="Sync Failures"
                )
            ],
            width=12,
            height=6
        )

        # Widget 2: Transfer Volume
        transfer_volume_widget = cloudwatch.GraphWidget(
            title="Transfer Volume (GB)",
            left=[
                cloudwatch.MathExpression(
                    expression="m1 / 1073741824",  # Convert bytes to GB
                    using_metrics={
                        "m1": cloudwatch.Metric(
                            namespace=self.namespace,
                            metric_name="BytesTransferred",
                            dimensions_map={
                                "Provider": "gopro",
                                "Environment": self.environment
                            },
                            statistic="Sum",
                            period=Duration.hours(1)
                        )
                    },
                    label="Bytes Transferred (GB)"
                )
            ],
            width=12,
            height=6
        )

        # Widget 3: Transfer Throughput
        throughput_widget = cloudwatch.GraphWidget(
            title="Transfer Throughput (Mbps)",
            left=[
                cloudwatch.Metric(
                    namespace=self.namespace,
                    metric_name="TransferThroughput",
                    dimensions_map={
                        "Provider": "gopro",
                        "Environment": self.environment
                    },
                    statistic="Average",
                    period=Duration.minutes(5),
                    label="Avg Throughput"
                )
            ],
            width=12,
            height=6
        )

        # Widget 4: Lambda Performance
        lambda_metrics = []
        for function_name, function in self.lambda_functions.items():
            lambda_metrics.extend([
                function.metric_duration(
                    statistic="p50",
                    period=Duration.minutes(5),
                    label=f"{function_name} p50"
                ),
                function.metric_duration(
                    statistic="p99",
                    period=Duration.minutes(5),
                    label=f"{function_name} p99"
                )
            ])

        lambda_performance_widget = cloudwatch.GraphWidget(
            title="Lambda Performance (Duration)",
            left=lambda_metrics,
            width=12,
            height=6
        )

        # Widget 5: Error Rate
        error_metrics = []
        for function_name, function in self.lambda_functions.items():
            error_metrics.append(
                function.metric_errors(
                    statistic="Sum",
                    period=Duration.minutes(5),
                    label=f"{function_name} Errors"
                )
            )

        error_rate_widget = cloudwatch.GraphWidget(
            title="Error Rate by Function",
            left=error_metrics,
            width=12,
            height=6,
            stacked=True
        )

        # Widget 6: Step Functions Executions
        sfn_executions_widget = cloudwatch.SingleValueWidget(
            title="Step Functions Executions (24h)",
            metrics=[
                self.state_machine.metric_succeeded(
                    statistic="Sum",
                    period=Duration.days(1),
                    label="Succeeded"
                ),
                self.state_machine.metric_failed(
                    statistic="Sum",
                    period=Duration.days(1),
                    label="Failed"
                )
            ],
            width=12,
            height=6
        )

        # Widget 7: Secrets Rotation Status
        rotation_widget = cloudwatch.GraphWidget(
            title="Secrets Rotation Status",
            left=[
                cloudwatch.Metric(
                    namespace="CloudSync/SecretsRotation",
                    metric_name="RotationSuccess",
                    dimensions_map={
                        "Provider": "gopro"
                    },
                    statistic="Sum",
                    period=Duration.days(1),
                    label="Successful Rotations"
                ),
                cloudwatch.Metric(
                    namespace="CloudSync/SecretsRotation",
                    metric_name="RotationFailure",
                    dimensions_map={
                        "Provider": "gopro"
                    },
                    statistic="Sum",
                    period=Duration.days(1),
                    label="Failed Rotations"
                )
            ],
            right=[
                cloudwatch.Metric(
                    namespace="CloudSync/SecretsRotation",
                    metric_name="RotationDuration",
                    dimensions_map={
                        "Provider": "gopro"
                    },
                    statistic="Average",
                    period=Duration.days(1),
                    label="Rotation Duration (s)"
                )
            ],
            width=12,
            height=6
        )

        # Add all widgets to dashboard
        dashboard.add_widgets(sync_success_widget, transfer_volume_widget)
        dashboard.add_widgets(throughput_widget, lambda_performance_widget)
        dashboard.add_widgets(error_rate_widget, sfn_executions_widget)
        dashboard.add_widgets(rotation_widget)

    def _create_logs_insights_queries(self) -> None:
        """Create saved CloudWatch Logs Insights queries."""
        
        # Get log group names
        log_groups = [
            f"/aws/lambda/{func.function_name}"
            for func in self.lambda_functions.values()
        ]
        log_groups.append(f"/aws/vendedlogs/states/{self.state_machine.state_machine_name}")

        # Convert log group names to LogGroup references
        log_group_refs = [
            logs.LogGroup.from_log_group_name(
                self, 
                f"LogGroup{i}", 
                log_group_name
            )
            for i, log_group_name in enumerate(log_groups)
        ]

        # Query 1: Failed Downloads in Last 24 Hours
        logs.QueryDefinition(
            self,
            "FailedDownloadsQuery",
            query_definition_name=f"{self.environment}-Failed-Downloads-24h",
            query_string="""fields @timestamp, media_id, filename, error_message
| filter level = "ERROR" and event_type = "video_download_failed"
| sort @timestamp desc
| limit 100""",
            log_groups=log_group_refs
        )

        # Query 2: Average Transfer Throughput
        logs.QueryDefinition(
            self,
            "AvgThroughputQuery",
            query_definition_name=f"{self.environment}-Average-Throughput",
            query_string="""fields media_id, bytes_transferred, transfer_duration_seconds,
       (bytes_transferred / transfer_duration_seconds / 1048576) as throughput_mbps
| filter event_type = "video_download_complete"
| stats avg(throughput_mbps) as avg_throughput,
        max(throughput_mbps) as max_throughput,
        min(throughput_mbps) as min_throughput""",
            log_groups=log_group_refs
        )

        # Query 3: Slow Transfers
        logs.QueryDefinition(
            self,
            "SlowTransfersQuery",
            query_definition_name=f"{self.environment}-Slow-Transfers",
            query_string="""fields @timestamp, media_id, filename, file_size_bytes, transfer_duration_seconds
| filter event_type = "video_download_complete"
        and file_size_bytes < 524288000
        and transfer_duration_seconds > 120
| sort transfer_duration_seconds desc""",
            log_groups=log_group_refs
        )

    def _configure_log_retention(self) -> None:
        """Configure log retention for Lambda functions."""
        
        for function_name, function in self.lambda_functions.items():
            logs.LogRetention(
                self,
                f"{function_name}LogRetention",
                log_group_name=f"/aws/lambda/{function.function_name}",
                retention=logs.RetentionDays.ONE_MONTH
            )
