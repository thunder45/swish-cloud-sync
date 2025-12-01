"""
Orchestration Construct

Creates and configures the Step Functions state machine for workflow orchestration.
Updated for cookie-based authentication with token-validator Lambda.
"""

from aws_cdk import (
    Duration,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_iam as iam,
    aws_logs as logs,
    aws_events as events,
    aws_events_targets as targets,
)
from constructs import Construct
from typing import Optional


class OrchestrationConstruct(Construct):
    """Construct for Step Functions orchestration"""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        token_validator: lambda_.Function,
        media_lister: lambda_.Function,
        video_downloader: lambda_.Function,
        sns_topic: Optional[sns.ITopic] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.token_validator = token_validator
        self.media_lister = media_lister
        self.video_downloader = video_downloader
        self.sns_topic = sns_topic

        # Create state machine
        self.state_machine = self._create_state_machine()

        # Create EventBridge scheduler
        self.scheduler_rule = self._create_scheduler()

    def _create_state_machine(self) -> sfn.StateMachine:
        """Create Step Functions state machine for sync orchestration"""

        # Create execution role with explicit permissions
        execution_role = iam.Role(
            self,
            "StateMachineExecutionRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            description="Execution role for GoPro Sync state machine",
        )

        # Grant Lambda invoke permissions
        self.token_validator.grant_invoke(execution_role)
        self.media_lister.grant_invoke(execution_role)
        self.video_downloader.grant_invoke(execution_role)

        # Grant SNS publish if topic exists
        if self.sns_topic:
            self.sns_topic.grant_publish(execution_role)

        # Grant CloudWatch Logs permissions
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogDelivery",
                    "logs:GetLogDelivery",
                    "logs:UpdateLogDelivery",
                    "logs:DeleteLogDelivery",
                    "logs:ListLogDeliveries",
                    "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies",
                    "logs:DescribeLogGroups",
                ],
                resources=["*"],
            )
        )

        # Grant X-Ray permissions
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="XRayAccess",
                effect=iam.Effect.ALLOW,
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )

        # ===== STATE DEFINITIONS =====
        
        # Generate correlation ID at start of execution
        generate_correlation_id = sfn.Pass(
            self,
            "GenerateCorrelationId",
            parameters={
                "correlation_id.$": "$$.Execution.Name",
                "execution_id.$": "$$.Execution.Id",
                "start_time.$": "$$.Execution.StartTime",
                "provider": "gopro",
            },
            result_path="$.context",
        )

        # Validate tokens (checks cookie validity)
        validate_tokens_task = tasks.LambdaInvoke(
            self,
            "ValidateTokens",
            lambda_function=self.token_validator,
            payload=sfn.TaskInput.from_object(
                {
                    "correlation_id.$": "$.context.correlation_id",
                }
            ),
            result_path="$.validation",
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
                "valid.$": "$.Payload.valid",
                "cookie_age_days.$": "$.Payload.cookie_age_days",
                "validation_method.$": "$.Payload.validation_method",
                "duration_seconds.$": "$.Payload.duration_seconds",
            },
            retry_on_service_exceptions=True,
        )

        # Add retry configuration for token validation
        validate_tokens_task.add_retry(
            errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException"],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        # Check if tokens are valid
        check_token_validity = sfn.Choice(self, "CheckTokenValidity")

        tokens_invalid = sfn.Fail(
            self,
            "TokensInvalid",
            error="TokenValidationFailed",
            cause="Cookies are invalid or expired. Manual refresh required.",
        )

        # List media from GoPro (no auth parameters needed - Lambda gets from Secrets Manager)
        list_media_task = tasks.LambdaInvoke(
            self,
            "ListMedia",
            lambda_function=self.media_lister,
            payload=sfn.TaskInput.from_object(
                {
                    "provider": "gopro",
                    "correlation_id.$": "$.context.correlation_id",
                }
            ),
            result_path="$.media",
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
                "provider.$": "$.Payload.provider",
                "new_videos.$": "$.Payload.new_videos",
                "total_found.$": "$.Payload.total_found",
                "new_count.$": "$.Payload.new_count",
                "already_synced.$": "$.Payload.already_synced",
            },
            retry_on_service_exceptions=True,
        )

        # Add retry configuration for media listing
        list_media_task.add_retry(
            errors=["Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        # Check if there are new videos to download
        check_new_videos = sfn.Choice(self, "CheckNewVideos")

        no_new_videos = sfn.Succeed(
            self,
            "NoNewVideos",
            comment="No new videos to sync - execution completed successfully",
        )

        # Download video task (used in Map state)
        # Lambda gets credentials from Secrets Manager, no auth parameters needed
        download_video_task = tasks.LambdaInvoke(
            self,
            "DownloadVideo",
            lambda_function=self.video_downloader,
            payload=sfn.TaskInput.from_object(
                {
                    "provider": "gopro",
                    "media_id": sfn.JsonPath.string_at("$.video.media_id"),
                    "filename": sfn.JsonPath.string_at("$.video.filename"),
                    "download_url": sfn.JsonPath.string_at("$.video.download_url"),
                    "file_size": sfn.JsonPath.number_at("$.video.file_size"),
                    "upload_date": sfn.JsonPath.string_at("$.video.upload_date"),
                    "duration": sfn.JsonPath.number_at("$.video.duration"),
                    "correlation_id.$": "$.correlation_id",
                }
            ),
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
                "media_id.$": "$.Payload.media_id",
                "s3_key.$": "$.Payload.s3_key",
                "bytes_transferred.$": "$.Payload.bytes_transferred",
                "transfer_duration.$": "$.Payload.transfer_duration",
                "error.$": "$.Payload.error",
            },
            retry_on_service_exceptions=False,  # Custom retry logic below
        )

        # Add retry configuration for video download
        download_video_task.add_retry(
            errors=["NetworkError", "TimeoutError"],
            interval=Duration.seconds(30),
            max_attempts=3,
            backoff_rate=2.0,
            max_delay=Duration.seconds(300),
        )

        # Mark video as failed (fallback for download errors)
        mark_video_failed = sfn.Pass(
            self,
            "MarkVideoFailed",
            parameters={
                "status": "FAILED",
                "media_id.$": "$.media_id",
                "error.$": "$.error",
            },
        )

        # Mark video as complete
        mark_video_complete = sfn.Pass(
            self,
            "VideoComplete",
            parameters={
                "status": "COMPLETED",
                "media_id.$": "$.media_id",
                "s3_key.$": "$.s3_key",
                "bytes_transferred.$": "$.bytes_transferred",
            },
        )

        # Add catch for download errors
        download_video_task.add_catch(
            mark_video_failed,
            errors=["States.ALL"],
            result_path="$.error",
        )

        # Chain download task to completion
        download_video_task.next(mark_video_complete)

        # Map state for parallel downloads (max 5 concurrent)
        download_videos_map = sfn.Map(
            self,
            "DownloadVideos",
            items_path="$.media.new_videos",
            max_concurrency=5,
            result_path="$.download_results",
            parameters={
                "video.$": "$$.Map.Item.Value",
                "correlation_id.$": "$.context.correlation_id",
            },
        )
        download_videos_map.iterator(download_video_task)

        # Generate summary of execution
        generate_summary = sfn.Pass(
            self,
            "GenerateSummary",
            parameters={
                "execution_id.$": "$.context.execution_id",
                "correlation_id.$": "$.context.correlation_id",
                "start_time.$": "$.context.start_time",
                "total_videos.$": "$.media.new_count",
                "total_found.$": "$.media.total_found",
                "already_synced.$": "$.media.already_synced",
                "download_results.$": "$.download_results",
                "cookie_age_days.$": "$.validation.cookie_age_days",
            },
            result_path="$.summary",
        )

        # Check for failures in download results
        check_for_failures = sfn.Choice(self, "CheckForFailures")

        # Calculate if there were any failures (statusCode != 200)
        # Note: This is a simplified check - individual failures are logged in CloudWatch
        has_failures_condition = sfn.Condition.is_present("$.download_results[?(@.statusCode != 200)]")

        sync_complete = sfn.Succeed(
            self,
            "SyncComplete",
            comment="Sync completed successfully",
        )

        # Notify partial failure (if SNS topic is configured)
        if self.sns_topic:
            notify_partial_failure = tasks.SnsPublish(
                self,
                "NotifyPartialFailure",
                topic=self.sns_topic,
                subject="GoPro Sync Partial Failure",
                message=sfn.TaskInput.from_object(
                    {
                        "execution_id": sfn.JsonPath.string_at("$.summary.execution_id"),
                        "correlation_id": sfn.JsonPath.string_at("$.summary.correlation_id"),
                        "total_videos": sfn.JsonPath.number_at("$.summary.total_videos"),
                        "start_time": sfn.JsonPath.string_at("$.summary.start_time"),
                        "message": "Sync completed with failures. Check CloudWatch Logs for details.",
                        "cookie_age_days": sfn.JsonPath.number_at("$.summary.cookie_age_days"),
                    }
                ),
            )
            notify_partial_failure.next(sync_complete)

            notify_critical_failure = tasks.SnsPublish(
                self,
                "NotifyCriticalFailure",
                topic=self.sns_topic,
                subject="GoPro Sync Critical Failure",
                message=sfn.TaskInput.from_object(
                    {
                        "execution_id": sfn.JsonPath.string_at("$.context.execution_id"),
                        "correlation_id": sfn.JsonPath.string_at("$.context.correlation_id"),
                        "error_cause": sfn.JsonPath.string_at("$.error.Cause"),
                        "error_details": sfn.JsonPath.string_at("$.error.Error"),
                        "message": "Critical failure in sync execution. Manual intervention required.",
                    }
                ),
            )

            sync_failed = sfn.Fail(
                self,
                "SyncFailed",
                error="SyncExecutionFailed",
                cause="Critical failure during sync execution",
            )
            notify_critical_failure.next(sync_failed)

            # Add catch blocks for critical failures
            validate_tokens_task.add_catch(
                notify_critical_failure,
                errors=["States.ALL"],
                result_path="$.error",
            )
            list_media_task.add_catch(
                notify_critical_failure,
                errors=["States.ALL"],
                result_path="$.error",
            )

            # For failures after downloads, notify but still complete
            check_for_failures_definition = (
                check_for_failures
                .when(has_failures_condition, notify_partial_failure)
                .otherwise(sync_complete)
            )
        else:
            # If no SNS topic, just succeed or fail
            sync_failed = sfn.Fail(
                self,
                "SyncFailed",
                error="SyncExecutionFailed",
                cause="Critical failure during sync execution",
            )

            validate_tokens_task.add_catch(
                sync_failed,
                errors=["States.ALL"],
                result_path="$.error",
            )
            list_media_task.add_catch(
                sync_failed,
                errors=["States.ALL"],
                result_path="$.error",
            )

            # Simplified - always succeed after downloads
            check_for_failures_definition = check_for_failures.otherwise(sync_complete)

        # Build the state machine flow
        definition = (
            generate_correlation_id
            .next(validate_tokens_task)
            .next(
                check_token_validity
                .when(
                    sfn.Condition.boolean_equals("$.validation.valid", True),
                    list_media_task.next(
                        check_new_videos
                        .when(
                            sfn.Condition.number_greater_than("$.media.new_count", 0),
                            download_videos_map
                            .next(generate_summary)
                            .next(check_for_failures_definition),
                        )
                        .otherwise(no_new_videos)
                    ),
                )
                .otherwise(tokens_invalid)
            )
        )

        # Create CloudWatch log group for state machine
        log_group = logs.LogGroup(
            self,
            "StateMachineLogGroup",
            log_group_name="/aws/states/gopro-sync-orchestrator",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        # Create state machine with explicit role
        state_machine = sfn.StateMachine(
            self,
            "GoProSyncOrchestrator",
            state_machine_name="gopro-sync-orchestrator",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(12),
            tracing_enabled=True,
            role=execution_role,
            logs=sfn.LogOptions(
                destination=log_group,
                level=sfn.LogLevel.ALL,
                include_execution_data=True,
            ),
        )

        return state_machine

    def _create_scheduler(self) -> events.Rule:
        """Create EventBridge rule to trigger state machine daily at 2 AM CET"""

        # Create EventBridge rule with cron expression
        # Cron format: minute hour day-of-month month day-of-week year
        # 2 AM CET = 1 AM UTC (CET is UTC+1, CEST is UTC+2)
        # Using 1 AM UTC to approximate 2 AM CET (adjust for daylight saving if needed)
        rule = events.Rule(
            self,
            "DailySyncSchedule",
            rule_name="gopro-sync-daily-schedule",
            description="Trigger GoPro sync workflow daily at 2 AM CET",
            schedule=events.Schedule.cron(
                minute="0",
                hour="1",  # 1 AM UTC = 2 AM CET (winter time)
                month="*",
                week_day="*",
                year="*",
            ),
            enabled=True,
        )

        # Add state machine as target
        # Empty input - Lambdas get credentials from Secrets Manager
        rule.add_target(
            targets.SfnStateMachine(
                self.state_machine,
                input=events.RuleTargetInput.from_object({}),
            )
        )

        return rule
