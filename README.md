# Insurance Customer Support AI Agent

> **Production-grade** insurance support system using AWS Bedrock (Claude 3.5 Sonnet), SageMaker, and RAG with human-in-the-loop validation.

## Architecture

```
[Customer] → (Email / WhatsApp / Chat)
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  Ingestion Layer  (SES / API Gateway / SNS) │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│  Orchestrator   (AWS Step Functions)        │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │PII Redact│ →│Classify  │→ │RAG Search │  │
│  └──────────┘  └──────────┘  └───────────┘  │
│       │              │              │       │
│       ▼              ▼              ▼       │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │Guardrails│→ │Generate  │→ │ Validate  │  │
│  └──────────┘  └──────────┘  └───────────┘  │
│                      │                      │
│            [Auto/HITL Approval]             │
└──────────────────┬──────────────────────────┘
                   ▼
┌──────────────────────────────────────────────┐
│  Output Layer   (SES Response / DynamoDB)    │
│  Audit & Learn  (S3 Logs → Fine-tuning)      │
└──────────────────────────────────────────────┘
```

## Project Structure

```
RAG_Insurance/
├── config/              # Settings & prompt templates
│   ├── settings.py      # Pydantic env-based configuration
│   └── prompts.py       # All LLM prompts (auditable)
├── schemas/             # Pydantic data models
│   ├── ticket.py        # NormalizedTicket, TicketStatus
│   ├── classification.py# IntentType, escalation rules
│   └── response.py      # DraftResponse, FeedbackSignal
├── lambdas/             # AWS Lambda functions
│   ├── ingestion/       # Email (SES), Webhook (API GW)
│   ├── preprocessing/   # PII redaction, Intent classification
│   └── orchestration/   # HITL callback, Response sender, Feedback
├── rag/                 # RAG pipeline
│   ├── embeddings.py    # Bedrock Titan Embeddings
│   ├── vector_store.py  # OpenSearch Serverless k-NN
│   ├── retriever.py     # Strict-mode retrieval
│   └── indexing_pipeline.py  # Document → chunks → embeddings → index
├── llm/                 # LLM generation & safety
│   ├── generator.py     # Bedrock Claude 3.5 Sonnet
│   ├── guardrails.py    # Payout promises, hallucination, toxicity
│   └── prompt_templates.py
├── orchestration/       # Step Functions ASL definition
│   └── state_machine.py
├── dashboard/           # Streamlit HITL review UI
│   ├── app.py
│   └── auth.py          # Cognito auth
├── infra/               # AWS CDK (Infrastructure as Code)
│   ├── app.py           # CDK entrypoint
│   └── stacks/          # 7 stacks (Network, Security, Storage, etc.)
└── tests/
    ├── unit/            # PII, classifier, guardrails, retriever
    └── integration/     # End-to-end pipeline simulation
```

## Quick Start

### Prerequisites

- Python 3.11+
- AWS CLI configured (with appropriate permissions)
- AWS CDK CLI: `npm install -g aws-cdk`

### Install

```bash
cd RAG_Insurance
pip install -e ".[dev,dashboard,indexing]"
```

### Run Tests

```bash
pytest tests/ -v
```

### Index Knowledge Base

```bash
python -m rag.indexing_pipeline \
  --source-dir ./documents/policies \
  --index policy-documents \
  --doc-type policy \
  --create-index
```

### Launch Review Dashboard

```bash
streamlit run dashboard/app.py
```

### Deploy Infrastructure

```bash
cd infra
cdk synth --all          # Validate templates
cdk deploy --all         # Deploy to AWS
```

## Key Design Decisions

| Decision                     | Rationale                                                                  |
| ---------------------------- | -------------------------------------------------------------------------- |
| **Strict RAG mode**          | If no relevant context found, defer to human — never hallucinate           |
| **Dual PII backend**         | Comprehend (managed) + SageMaker NER (insurance-specific patterns)         |
| **Keyword escalation**       | Words like "lawyer", "fraud", "sue" force HITL regardless of confidence    |
| **Payout promise guardrail** | AI must NEVER promise specific amounts — critical for insurance compliance |
| **Step Functions callbacks** | HITL uses SQS + callback pattern for async human review                    |

## Security

- **Encryption**: All S3 buckets and DynamoDB tables use KMS (CMK) encryption
- **Network**: All compute runs in private VPC with VPC endpoints only
- **Auth**: Cognito with MFA required for review dashboard access
- **Audit**: All prompts, responses, and PII events logged to S3 (immutable)
- **IAM**: Least-privilege roles per Lambda function

## Environment Variables

| Variable                   | Description                     |
| -------------------------- | ------------------------------- |
| `AWS_REGION`               | AWS region (default: us-east-1) |
| `BEDROCK_GENERATION_MODEL` | Claude model ID                 |
| `BEDROCK_EMBEDDING_MODEL`  | Titan Embeddings model ID       |
| `OPENSEARCH_ENDPOINT`      | OpenSearch Serverless endpoint  |
| `DYNAMODB_TICKETS_TABLE`   | Tickets table name              |
| `SNS_ORCHESTRATION_TOPIC`  | Pipeline trigger topic ARN      |
| `SES_SENDER_EMAIL`         | Verified SES sender address     |
