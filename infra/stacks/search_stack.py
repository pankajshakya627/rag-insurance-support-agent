"""
Search Stack — OpenSearch Serverless for vector search (RAG).
"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_kms as kms,
    aws_opensearchserverless as aoss,
)
from constructs import Construct


class SearchStack(cdk.Stack):
    """OpenSearch Serverless collection for vector-based knowledge retrieval."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        kms_key: kms.IKey,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        collection_name = "insurance-knowledge-base"

        # ---- Encryption Policy ----
        encryption_policy = aoss.CfnSecurityPolicy(
            self,
            "EncryptionPolicy",
            name=f"{collection_name}-enc",
            type="encryption",
            policy=json.dumps({
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{collection_name}"],
                    }
                ],
                "AWSOwnedKey": False,
                "KmsARN": kms_key.key_arn,
            }),
        )

        # ---- Network Policy ----
        network_policy = aoss.CfnSecurityPolicy(
            self,
            "NetworkPolicy",
            name=f"{collection_name}-net",
            type="network",
            policy=json.dumps([
                {
                    "Description": "VPC access for insurance knowledge base",
                    "Rules": [
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{collection_name}"],
                        },
                        {
                            "ResourceType": "dashboard",
                            "Resource": [f"collection/{collection_name}"],
                        },
                    ],
                    "AllowFromPublic": False,
                    "SourceVPCEs": [],  # VPC Endpoint IDs added post-creation
                }
            ]),
        )

        # ---- Data Access Policy ----
        data_access_policy = aoss.CfnAccessPolicy(
            self,
            "DataAccessPolicy",
            name=f"{collection_name}-access",
            type="data",
            policy=json.dumps([
                {
                    "Description": "Access for Insurance AI application",
                    "Rules": [
                        {
                            "ResourceType": "index",
                            "Resource": [f"index/{collection_name}/*"],
                            "Permission": [
                                "aoss:CreateIndex",
                                "aoss:DeleteIndex",
                                "aoss:UpdateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument",
                            ],
                        },
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{collection_name}"],
                            "Permission": [
                                "aoss:CreateCollectionItems",
                                "aoss:DescribeCollectionItems",
                                "aoss:UpdateCollectionItems",
                            ],
                        },
                    ],
                    "Principal": [f"arn:aws:iam::{cdk.Aws.ACCOUNT_ID}:root"],
                }
            ]),
        )

        # ---- Collection ----
        self.collection = aoss.CfnCollection(
            self,
            "KnowledgeBaseCollection",
            name=collection_name,
            type="VECTORSEARCH",
            description="Vector search collection for Insurance AI RAG pipeline",
        )

        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(network_policy)
        self.collection.add_dependency(data_access_policy)

        # ---- Outputs ----
        cdk.CfnOutput(
            self,
            "CollectionEndpoint",
            value=self.collection.attr_collection_endpoint,
        )
        cdk.CfnOutput(
            self,
            "CollectionArn",
            value=self.collection.attr_arn,
        )
