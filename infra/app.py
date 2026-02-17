#!/usr/bin/env python3
"""
AWS CDK App — Insurance AI Customer Support Agent.

Instantiates all infrastructure stacks.
"""

import aws_cdk as cdk

from infra.stacks.network_stack import NetworkStack
from infra.stacks.security_stack import SecurityStack
from infra.stacks.storage_stack import StorageStack
from infra.stacks.search_stack import SearchStack
from infra.stacks.ml_stack import MLStack
from infra.stacks.ingestion_stack import IngestionStack
from infra.stacks.orchestration_stack import OrchestrationStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or "123456789012",
    region=app.node.try_get_context("region") or "us-east-1",
)

# ---- Foundation Stacks ----
network = NetworkStack(app, "InsuranceAI-Network", env=env)
security = SecurityStack(app, "InsuranceAI-Security", env=env)
storage = StorageStack(
    app, "InsuranceAI-Storage", env=env,
    kms_key=security.kms_key,
)

# ---- Knowledge Base ----
search = SearchStack(
    app, "InsuranceAI-Search", env=env,
    vpc=network.vpc,
    kms_key=security.kms_key,
)

# ---- ML/AI ----
ml = MLStack(
    app, "InsuranceAI-ML", env=env,
    vpc=network.vpc,
)

# ---- Application ----
ingestion = IngestionStack(
    app, "InsuranceAI-Ingestion", env=env,
    vpc=network.vpc,
    raw_bucket=storage.raw_bucket,
    attachments_bucket=storage.attachments_bucket,
    tickets_table=storage.tickets_table,
)

orchestration = OrchestrationStack(
    app, "InsuranceAI-Orchestration", env=env,
    vpc=network.vpc,
    tickets_table=storage.tickets_table,
    audit_bucket=storage.audit_bucket,
    finetuning_bucket=storage.finetuning_bucket,
    ingestion_topic=ingestion.orchestration_topic,
)

# ---- Stack Dependencies ----
storage.add_dependency(security)
search.add_dependency(network)
ml.add_dependency(network)
ingestion.add_dependency(storage)
orchestration.add_dependency(ingestion)

app.synth()
