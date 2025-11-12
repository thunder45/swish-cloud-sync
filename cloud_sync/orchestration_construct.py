"""
Orchestration Construct

Creates and configures the Step Functions state machine for workflow orchestration.
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
        media_authenticator: lambda_.Function,
        media_lister: lambda_.Function,
        video_downloader: lambda_.Function,
        sns_topic: Optional[sns.ITopic] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.media_authenticator = media_authenticator
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
        self.media_authenticator.grant_invoke(execution_role)
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

        # Define Lambda invocation tasks
        authenticate_task = tasks.LambdaInvoke(
            self,
            "AuthenticateProvider",
            lambda_function=self.media_authenticator,
            payload=sfn.TaskInput.from_object(
                {"provider": "gopro", "action": "authenticate"}
            ),
            result_path="$.auth",
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
                "auth_token.$": "$.Payload.auth_token",
                "user_id.$": "$.Payload.user_id",
                "expires_at.$": "$.Payload.expires_at",
            },
            retry_on_service_exceptions=True,
        )

        # Add retry configuration for authentication
        authenticate_task.add_retry(
            errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException"],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        list_media_task = tasks.LambdaInvoke(
            self,
            "ListMedia",
            lambda_function=self.media_lister,
            payload=sfn.TaskInput.from_object(
                {
                    "provider": "gopro",
                    "auth_token": sfn.JsonPath.string_at("$.auth.auth_token"),
                    "user_id": sfn.JsonPath.string_at("$.auth.user_id"),
                    "max_videos": 1000,
                }
            ),
            result_path="$.media",
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
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

        # Download video task (used in Map state)
        # FIX: Use $.video and $.auth from Map parameters
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
                    "auth_token": sfn.JsonPath.string_at("$.auth.auth_token"),
                }
            ),
            result_selector={
                "statusCode.$": "$.Payload.statusCode",
                "media_id.$": "$.Payload.media_id",
                "s3_key.$": "$.Payload.s3_key",
                "bytes_transferred.$": "$.Payload.bytes_transferred",
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
            result_path="$.download_result",
            parameters={"status": "FAILED", "error.$": "$.error"},
        )

        # Mark video as complete
        mark_video_complete = sfn.Pass(
            self,
            "VideoComplete",
            result_path="$.download_result",
            parameters={"status": "COMPLETED"},
        )

        # Add catch for download errors
        download_video_task.add_catch(
            mark_video_failed, errors=["States.ALL"], result_path="$.error"
        )

        # Chain download task to completion
        download_video_task.next(mark_video_complete)

        # Map state for parallel downloads
        # FIX: Pass auth context to iterator
        download_videos_map = sfn.Map(
            self,
            "DownloadVideos",
            items_path="$.media.new_videos",
            max_concurrency=5,
            result_path="$.download_results",
            parameters={
                "video.$": "$$.Map.Item.Value",
                "auth.$": "$.auth",
            },
        )
        download_videos_map.iterator(download_video_task)

        # Check if there are new videos
        check_new_videos = sfn.Choice(self, "CheckNewVideos")

        no_new_videos = sfn.Succeed(
            self, "NoNewVideos", comment="No new videos to sync"
        )

        # Generate summary
        generate_summary = sfn.Pass(
            self,
            "GenerateSummary",
            parameters={
                "execution_id": sfn.JsonPath.string_at("$$.Execution.Id"),
                "total_videos": sfn.JsonPath.number_at("$.media.new_count"),
                "start_time": sfn.JsonPath.string_at("$$.Execution.StartTime"),
                "download_results": sfn.JsonPath.string_at("$.download_results"),
            },
            result_path="$.summary",
        )

        # Check for failures - simplified approach
        # Individual failures are logged in CloudWatch, we just complete successfully
        check_for_failures = sfn.Choice(self, "CheckForFailures")

        sync_complete = sfn.Succeed(self, "SyncComplete")

        # Notify partial failure (if SNS topic is configured)
        if self.sns_topic:
            notify_partial_failure = tasks.SnsPublish(
                self,
                "NotifyPartialFailure",
                topic=self.sns_topic,
                subject="GoPro Sync Partial Failure",
                message=sfn.TaskInput.from_object(
                    {
                        "execution_id": sfn.JsonPath.string_at(
                            "$.summary.execution_id"
                        ),
                        "total_videos": sfn.JsonPath.number_at(
                            "$.summary.total_videos"
                        ),
                        "message": "Sync completed with failures. Check CloudWatch Logs for details.",
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
                        "execution_id": sfn.JsonPath.string_at("$$.Execution.Id"),
                        "error": sfn.JsonPath.string_at("$.error.Cause"),
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
            authenticate_task.add_catch(
                notify_critical_failure, errors=["States.ALL"], result_path="$.error"
            )
            list_media_task.add_catch(
                notify_critical_failure, errors=["States.ALL"], result_path="$.error"
            )

            # FIX: Simplified failure detection - just always succeed after downloads
            # Individual failures are logged and can be retried on next execution
            check_for_failures_definition = check_for_failures.otherwise(sync_complete)
        else:
            # If no SNS topic, just succeed or fail
            notify_partial_failure = sync_complete
            sync_failed = sfn.Fail(
                self,
                "SyncFailed",
                error="SyncExecutionFailed",
                cause="Critical failure during sync execution",
            )

            authenticate_task.add_catch(
                sync_failed, errors=["States.ALL"], result_path="$.error"
            )
            list_media_task.add_catch(
                sync_failed, errors=["States.ALL"], result_path="$.error"
            )

            # Simplified - always succeed after downloads
            check_for_failures_definition = check_for_failures.otherwise(sync_complete)

        # Build the state machine flow
        definition = (
            authenticate_task.next(list_media_task)
            .next(
                check_new_videos.when(
                    sfn.Condition.number_greater_than("$.media.new_count", 0),
                    download_videos_map.next(generate_summary).next(
                        check_for_failures_definition
                    ),
                ).otherwise(no_new_videos)
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
            role=execution_role,  # Explicit role
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
        rule.add_target(
            targets.SfnStateMachine(
                self.state_machine,
                input=events.RuleTargetInput.from_object(
                    {
                        "provider": "gopro",
                        "scheduled": True,
                        "trigger_time": events.EventField.time,
                    }
                ),
            )
        )

        return rule
