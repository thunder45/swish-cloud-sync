"""VPC infrastructure construct for Cloud Sync Application."""

from aws_cdk import (
    aws_ec2 as ec2,
)
from constructs import Construct
from typing import Optional


class VPCConstruct(Construct):
    """Construct for VPC infrastructure (optional, for production environments)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs
    ) -> None:
        """Initialize VPC construct.
        
        Args:
            scope: CDK scope
            construct_id: Construct identifier
            **kwargs: Additional construct properties
        """
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC with public and private subnets
        self.vpc = self._create_vpc()

        # Create security groups
        self.lambda_security_group = self._create_lambda_security_group()
        self.vpc_endpoint_security_group = self._create_vpc_endpoint_security_group()

        # Create VPC endpoints
        self._create_vpc_endpoints()

    def _create_vpc(self) -> ec2.Vpc:
        """Create VPC with public and private subnets.
        
        Returns:
            VPC construct
        """
        vpc = ec2.Vpc(
            self,
            "CloudSyncVPC",
            max_azs=2,  # Use 2 availability zones for high availability
            nat_gateways=1,  # Single NAT Gateway to save costs
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ],
            enable_dns_hostnames=True,
            enable_dns_support=True
        )

        return vpc

    def _create_lambda_security_group(self) -> ec2.SecurityGroup:
        """Create security group for Lambda functions.
        
        Returns:
            Security group for Lambda functions
        """
        sg = ec2.SecurityGroup(
            self,
            "LambdaSecurityGroup",
            vpc=self.vpc,
            description="Security group for Cloud Sync Lambda functions",
            allow_all_outbound=True  # Required for GoPro API access
        )

        return sg

    def _create_vpc_endpoint_security_group(self) -> ec2.SecurityGroup:
        """Create security group for VPC endpoints.
        
        Returns:
            Security group for VPC endpoints
        """
        sg = ec2.SecurityGroup(
            self,
            "VPCEndpointSecurityGroup",
            vpc=self.vpc,
            description="Security group for VPC endpoints",
            allow_all_outbound=False
        )

        # Allow inbound HTTPS from Lambda security group
        sg.add_ingress_rule(
            peer=self.lambda_security_group,
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from Lambda functions"
        )

        return sg

    def _create_vpc_endpoints(self) -> None:
        """Create VPC endpoints for AWS services."""
        
        # S3 Gateway Endpoint (no additional cost)
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)]
        )

        # DynamoDB Gateway Endpoint (no additional cost)
        self.vpc.add_gateway_endpoint(
            "DynamoDBEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
            subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)]
        )

        # Secrets Manager Interface Endpoint
        self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.vpc_endpoint_security_group]
        )

        # CloudWatch Logs Interface Endpoint
        self.vpc.add_interface_endpoint(
            "CloudWatchLogsEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.vpc_endpoint_security_group]
        )

        # CloudWatch Monitoring Interface Endpoint
        self.vpc.add_interface_endpoint(
            "CloudWatchMonitoringEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.vpc_endpoint_security_group]
        )
