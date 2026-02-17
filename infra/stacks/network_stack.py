"""
Network Stack — VPC with private subnets and VPC endpoints.

All Lambda functions, SageMaker endpoints, and OpenSearch run
inside this VPC with no public internet access.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(cdk.Stack):
    """VPC and networking infrastructure."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- VPC ----
        self.vpc = ec2.Vpc(
            self,
            "InsuranceAI-VPC",
            vpc_name="insurance-ai-vpc",
            max_azs=2,
            nat_gateways=1,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # ---- VPC Endpoints (private access to AWS services) ----

        # S3 Gateway Endpoint
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        # DynamoDB Gateway Endpoint
        self.vpc.add_gateway_endpoint(
            "DynamoDBEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )

        # Bedrock Runtime Interface Endpoint
        self.vpc.add_interface_endpoint(
            "BedrockEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService(
                "bedrock-runtime"
            ),
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # SageMaker Runtime Interface Endpoint
        self.vpc.add_interface_endpoint(
            "SageMakerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_RUNTIME,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Comprehend Interface Endpoint
        self.vpc.add_interface_endpoint(
            "ComprehendEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService(
                "comprehend"
            ),
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # SQS Interface Endpoint
        self.vpc.add_interface_endpoint(
            "SQSEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SQS,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # SNS Interface Endpoint
        self.vpc.add_interface_endpoint(
            "SNSEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SNS,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Step Functions Interface Endpoint
        self.vpc.add_interface_endpoint(
            "StepFunctionsEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService(
                "states"
            ),
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # ---- Security Group for Lambda functions ----
        self.lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSG",
            vpc=self.vpc,
            description="Security group for Lambda functions",
            allow_all_outbound=True,
        )

        # ---- Outputs ----
        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
