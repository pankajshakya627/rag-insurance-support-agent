# AWS & Related Services — Deep Dive Guide

> **Purpose:** Explain every AWS service used in the Insurance AI Agent — what it is, why we chose it, how we use it, and interview Q&A.

---

## Table of Contents

| #   | Service                                                  | Category      | Role in System           |
| --- | -------------------------------------------------------- | ------------- | ------------------------ |
| 1   | [AWS Lambda](#1-aws-lambda)                              | Compute       | All business logic       |
| 2   | [Step Functions](#2-aws-step-functions)                  | Orchestration | Pipeline workflow        |
| 3   | [Amazon Bedrock](#3-amazon-bedrock)                      | AI/ML         | LLM + Embeddings         |
| 4   | [Amazon SES](#4-amazon-ses)                              | Messaging     | Email send/receive       |
| 5   | [Amazon S3](#5-amazon-s3)                                | Storage       | Documents, audit logs    |
| 6   | [Amazon DynamoDB](#6-amazon-dynamodb)                    | Database      | Tickets, state, profiles |
| 7   | [OpenSearch Serverless](#7-amazon-opensearch-serverless) | Search        | Vector store (RAG)       |
| 8   | [Amazon Comprehend](#8-amazon-comprehend)                | AI/ML         | PII detection            |
| 9   | [Amazon SageMaker](#9-amazon-sagemaker)                  | AI/ML         | Custom NER + classifier  |
| 10  | [Amazon SNS](#10-amazon-sns)                             | Messaging     | Event fan-out            |
| 11  | [Amazon SQS](#11-amazon-sqs)                             | Messaging     | HITL queue + DLQ         |
| 12  | [Amazon API Gateway](#12-amazon-api-gateway)             | Networking    | REST API                 |
| 13  | [Amazon Cognito](#13-amazon-cognito)                     | Security      | Dashboard auth           |
| 14  | [AWS KMS](#14-aws-kms)                                   | Security      | Encryption               |
| 15  | [Amazon VPC](#15-amazon-vpc)                             | Networking    | Network isolation        |
| 16  | [Amazon Textract](#16-amazon-textract)                   | AI/ML         | Document OCR             |
| 17  | [AWS CDK](#17-aws-cdk)                                   | DevOps        | Infrastructure as Code   |
| 18  | [Amazon CloudWatch](#18-amazon-cloudwatch)               | Monitoring    | Logs + alarms            |

---

## 1. AWS Lambda

### What It Is

A **serverless compute** service that runs code in response to events without provisioning servers. You pay only per invocation (per 1ms of compute).

### Why We Use It

- **Zero server management** — no patching, scaling, or capacity planning
- **Pay-per-use** — idle tickets cost $0
- **Event-driven** — triggers from SES, SNS, SQS, API Gateway
- **Sub-second cold starts** with Python 3.12 runtime

### How We Use It (8 Functions)

| Lambda                 | Trigger        | Memory  | Timeout |
| ---------------------- | -------------- | ------- | ------- |
| `email_handler`        | SNS (from SES) | 512 MB  | 30s     |
| `webhook_handler`      | API Gateway    | 256 MB  | 10s     |
| `attachment_processor` | Step Functions | 1024 MB | 120s    |
| `pii_redactor`         | Step Functions | 512 MB  | 60s     |
| `intent_classifier`    | Step Functions | 512 MB  | 30s     |
| `response_sender`      | Step Functions | 256 MB  | 30s     |
| `hitl_callback`        | API Gateway    | 256 MB  | 10s     |
| `feedback_handler`     | Step Functions | 256 MB  | 15s     |

### Interview Q&A

**Q: Why Lambda over ECS/Fargate or EC2?**

> Our workload is **request-driven with variable traffic** (emails spike during business hours). Lambda auto-scales from 0 to 1000 concurrent executions. ECS/Fargate would require minimum tasks always running ($$$). EC2 needs capacity planning and auto-scaling groups.
>
> | Factor       | Lambda         | ECS Fargate         | EC2                  |
> | ------------ | -------------- | ------------------- | -------------------- |
> | Cold start   | ~200ms         | ~30s (task startup) | N/A (always running) |
> | Min cost     | $0 (idle)      | ~$10/mo (1 task)    | ~$30/mo (t3.micro)   |
> | Max duration | 15 min         | Unlimited           | Unlimited            |
> | Scaling      | Automatic (ms) | Auto (minutes)      | Manual/ASG           |

**Q: What are Lambda cold starts and how do you mitigate them?**

> Cold start = first invocation after idle period. Lambda must download code, init runtime, import dependencies.
>
> - **Provisioned Concurrency** for latency-critical functions (classifier, generator)
> - **Keep imports lightweight** — lazy-load `boto3` clients inside functions
> - **Warm-up events** — CloudWatch scheduled rule pings functions every 5 min

**Q: What's the Lambda concurrency limit? What happens when you hit it?**

> Default: 1000 concurrent per region. If exceeded: `TooManyRequestsException` (429). Mitigated with **Reserved Concurrency** per function to prevent one Lambda from starving others.

---

## 2. AWS Step Functions

### What It Is

A **serverless orchestration** service that coordinates multiple AWS services into visual workflows using JSON-based Amazon States Language (ASL).

### Why We Use It

- **Visual debugging** — see exactly where a ticket failed in the console
- **Built-in retries** with exponential backoff
- **`waitForTaskToken`** — pause execution for HITL review (24h) at zero cost
- **Audit trail** — every state transition is logged
- **Exactly-once semantics** — Standard Workflows guarantee no duplicate processing

### Our State Machine

```
ProcessAttachments → RedactPII → ClassifyIntent → [Choice]
    ├── ForceHITL=True  → SendToHITLQueue (waits) → Resume
    └── ForceHITL=False → RetrieveContext → GenerateResponse → ValidateResponse
                                                                    ↓
                                                           [Auto/HITL Approval]
                                                                    ↓
                                                           SendResponse → AuditLog
```

### Interview Q&A

**Q: Why Step Functions over SQS fan-out or a custom orchestrator?**

> | Factor           | Step Functions             | SQS Fan-out                | Custom (Celery/Airflow) |
> | ---------------- | -------------------------- | -------------------------- | ----------------------- |
> | Visual debugging | ✅ Built-in                | ❌                         | ❌ Without add-on       |
> | HITL pause       | ✅ `waitForTaskToken`      | ⚠️ Visibility timeout hack | ✅ Manual               |
> | Error handling   | ✅ Declarative Retry/Catch | ⚠️ DLQ only                | ✅ Manual               |
> | Audit trail      | ✅ Every state logged      | ❌                         | ⚠️ Manual logging       |
> | Cost             | $0.025/1000 transitions    | $0.40/1M requests          | Server cost             |
> | Vendor lock-in   | ⚠️ AWS-specific            | Low                        | None                    |

**Q: Standard vs Express Workflows — which did you pick and why?**

> **Standard.** Our pipeline can wait 24h for HITL review. Express has a 5-min max duration. Standard also provides exactly-once execution guarantees, which prevents a ticket from being processed twice.

**Q: How does `waitForTaskToken` work?**

> 1. Step Functions enters a Task state with resource `arn:aws:states:::sqs:sendMessage.waitForTaskToken`
> 2. It generates a unique **task token** and embeds it in the SQS message
> 3. Execution **PAUSES** — zero compute cost during wait
> 4. Human reviews on dashboard → calls `sfn.send_task_success(taskToken, output)`
> 5. Execution **RESUMES** from where it paused

---

## 3. Amazon Bedrock

### What It Is

A **fully managed service** to access foundation models (Claude, Titan, Llama, etc.) via API — no infrastructure to manage. Models run inside AWS, so data never leaves your account.

### Why We Use It

- **Data stays in AWS** — HIPAA eligible, no external API calls
- **Two models, one service** — Claude for generation, Titan for embeddings
- **Guardrails API** — built-in content filtering (Layer 1-2 of our guardrail stack)
- **No GPU management** — unlike self-hosted LLMs on SageMaker

### Models We Use

| Model                        | Purpose                                                         | Input                  | Output          | Cost                              |
| ---------------------------- | --------------------------------------------------------------- | ---------------------- | --------------- | --------------------------------- |
| **Claude 4.6 Sonnet**        | Response generation, intent classification, hallucination check | Prompt (system + user) | JSON response   | $3/1M input, $15/1M output tokens |
| **Titan Text Embeddings V2** | Query & document embedding                                      | Text string            | 1024-dim vector | $0.02/1M tokens                   |

### Interview Q&A

**Q: Why Bedrock Claude over OpenAI GPT-4 or self-hosted Llama?**

> | Factor            | Bedrock Claude   | OpenAI GPT-4       | Self-hosted Llama   |
> | ----------------- | ---------------- | ------------------ | ------------------- |
> | Data residency    | ✅ AWS VPC       | ❌ US data centers | ✅ Your VPC         |
> | HIPAA             | ✅ BAA available | ❌                 | ✅ (if configured)  |
> | Ops overhead      | Zero             | Zero               | High (GPU, scaling) |
> | Cost              | Pay-per-token    | Pay-per-token      | GPU instances 24/7  |
> | Structured output | Excellent        | Excellent          | Good                |
>
> For **insurance** — regulatory compliance and data residency are non-negotiable. Bedrock is the only option that gives us zero-ops + data stays in VPC.

**Q: What are Bedrock Guardrails?**

> A managed content filtering layer that sits between your application and the model. You define policies (no hate speech, no PII, topic restrictions) and Bedrock enforces them automatically. We use it as Layers 1-2 of our 5-layer guardrail stack.

**Q: How do you handle Bedrock throttling?**

> Bedrock has per-model token-per-minute (TPM) limits. We handle this with:
>
> 1. **Step Functions retries** — `IntervalSeconds: 2, BackoffRate: 2.0, MaxAttempts: 3`
> 2. **Provisioned Throughput** (if needed) — reserved model capacity
> 3. **Graceful degradation** — if generation fails, return safe template and force HITL

---

## 4. Amazon SES

### What It Is

**Simple Email Service** — a cloud email service for sending and receiving emails at scale ($0.10 per 1,000 emails).

### Why We Use It

- **Native AWS integration** — Receipt Rules trigger Lambda directly
- **Production-scale** — handles thousands of emails/day
- **DKIM/SPF/DMARC** — built-in email authentication

### How We Use It

| Direction    | Purpose                 | Flow                                             |
| ------------ | ----------------------- | ------------------------------------------------ |
| **Inbound**  | Receive customer emails | SES Receipt Rule → S3 (store raw) → SNS → Lambda |
| **Outbound** | Send approved responses | Lambda → SES `send_email()` → Customer inbox     |

### Interview Q&A

**Q: How do you handle email bounces and complaints?**

> SES publishes bounce/complaint notifications to an SNS topic. We subscribe a Lambda that:
>
> 1. Marks the customer profile as `do_not_email` for hard bounces
> 2. Logs complaints for compliance review
> 3. Removes suppressed addresses before sending

**Q: What's the SES sending limit?**

> New accounts: 200 emails/day (sandbox). Production: request increase to 50,000+/day. We use **dedicated IPs** and **DKIM signing** to maintain sender reputation.

---

## 5. Amazon S3

### What It Is

**Simple Storage Service** — infinitely scalable object storage with 99.999999999% (11 nines) durability.

### Why We Use It

- **Durability** — 11 nines means virtually zero data loss
- **Object Lock** — WORM (Write Once Read Many) for compliance audit logs
- **Lifecycle policies** — auto-archive to Glacier after 90 days
- **Encryption** — SSE-KMS with customer-managed keys

### Our Buckets

| Bucket            | Contents                              | Retention         | Encryption | Object Lock        |
| ----------------- | ------------------------------------- | ----------------- | ---------- | ------------------ |
| `raw-messages`    | Original emails                       | 1 year → Glacier  | KMS        | ✅ Compliance mode |
| `attachments`     | Customer attachments (PDFs, images)   | 1 year            | KMS        | ❌                 |
| `audit-logs`      | Complete interaction records          | 7 years → Glacier | KMS        | ✅ Governance mode |
| `finetuning-data` | Approved Q&A pairs for model training | Indefinite        | KMS        | ❌                 |

### Interview Q&A

**Q: Why S3 Object Lock for audit logs?**

> Insurance regulations (e.g., NAIC Model Regulation) require 7-year retention of customer interaction records. Object Lock in **Compliance mode** prevents deletion by anyone — including the root account. This satisfies regulatory immutability requirements.

**Q: How do you minimize S3 costs?**

> 1. **Intelligent-Tiering** — automatically moves objects between access tiers
> 2. **Lifecycle rules** — Standard → IA after 30 days → Glacier after 90 days
> 3. **Multipart uploads** — for large attachments (>100MB)
> 4. **S3 VPC Endpoint** — no data transfer charges for intra-VPC access

---

## 6. Amazon DynamoDB

### What It Is

A **serverless NoSQL** key-value and document database with single-digit millisecond latency at any scale.

### Why We Use It

- **Serverless** — no capacity planning, scales automatically
- **Single-digit ms latency** — fast ticket lookups by ID
- **On-demand billing** — pay per read/write, $0 when idle
- **Streams** — real-time change events for analytics downstream

### Our Tables

| Table               | Partition Key     | Sort Key      | GSIs                             | Purpose                 |
| ------------------- | ----------------- | ------------- | -------------------------------- | ----------------------- |
| `Tickets`           | `ticket_id`       | `created_at`  | `status-index`, `customer-index` | Core ticket data        |
| `ConversationState` | `conversation_id` | `turn_number` | —                                | Multi-turn chat context |
| `CustomerProfiles`  | `customer_id`     | —             | `email-index`                    | Customer lookup         |

### Interview Q&A

**Q: Why DynamoDB over PostgreSQL (RDS)?**

> | Factor             | DynamoDB                   | RDS PostgreSQL                    |
> | ------------------ | -------------------------- | --------------------------------- |
> | Scaling            | Automatic, infinite        | Manual (read replicas, vertical)  |
> | Operations         | Zero (serverless)          | Patching, backups, failover       |
> | Latency            | Single-digit ms            | ~5-10ms (with connection pooling) |
> | Schema             | Flexible (schema-per-item) | Fixed (migrations needed)         |
> | Cost at low volume | ~$0 (on-demand)            | ~$15/mo minimum (db.t3.micro)     |
> | Complex queries    | ❌ Scan is expensive       | ✅ SQL joins, aggregations        |
>
> **Trade-off:** We sacrifice complex querying for operational simplicity. All our access patterns are key-value lookups (by ticket_id, customer_id), which DynamoDB excels at.

**Q: How do you handle DynamoDB hot partitions?**

> Not an issue for our workload — ticket_ids are UUIDs (uniformly distributed). If we had a hot key (e.g., a popular customer), we'd use **write sharding** with a random suffix.

**Q: What about DynamoDB Streams?**

> We use Streams to capture ticket status changes and pipe them to CloudWatch for real-time dashboards (tickets created/resolved per hour).

---

## 7. Amazon OpenSearch Serverless

### What It Is

A **serverless vector database** based on OpenSearch with built-in k-NN (k-Nearest Neighbor) search. Auto-scales compute (OCUs) based on workload.

### Why We Use It

- **Vector search** — native k-NN plugin for RAG similarity search
- **Serverless** — scales to zero OCUs when idle (with minimum of 2 OCUs)
- **VPC Endpoint** — private access, no public internet exposure
- **Multi-index** — separate indices for policies, tickets, compliance rules

### Our Configuration

| Setting         | Value           | Rationale                             |
| --------------- | --------------- | ------------------------------------- |
| Collection type | `VECTORSEARCH`  | Optimized for k-NN workloads          |
| Engine          | `nmslib` (HNSW) | Fastest ANN algorithm for 1024-dim    |
| Dimensions      | 1024            | Titan V2 output dimension             |
| Similarity      | Cosine          | Best for normalized text embeddings   |
| Top-K           | 5               | Balance: enough context without noise |
| Threshold       | 0.7             | Strict mode cutoff                    |

### Interview Q&A

**Q: Why OpenSearch Serverless over Pinecone, pgvector, or FAISS?**

> | Factor             | OpenSearch Serverless | Pinecone  | pgvector          | FAISS          |
> | ------------------ | --------------------- | --------- | ----------------- | -------------- |
> | AWS native         | ✅                    | ❌ (SaaS) | ⚠️ (RDS)          | ❌ (in-memory) |
> | VPC support        | ✅ Endpoint           | ❌ Public | ✅ RDS VPC        | N/A            |
> | Serverless         | ✅                    | ✅        | ❌ (RDS instance) | ❌             |
> | Metadata filtering | ✅                    | ✅        | ✅                | ❌             |
> | Scale-to-zero      | ⚠️ (min 2 OCU)        | ✅        | ❌                | N/A            |
> | HIPAA              | ✅                    | ❌        | ✅                | N/A            |

**Q: What's HNSW and why use it?**

> **Hierarchical Navigable Small World** — a graph-based ANN algorithm. Builds a multi-layer graph where higher layers have fewer, longer-range connections. Query navigates top→bottom for logarithmic search time. Recall ~99% for our 1024-dim, 100K-document corpus.

---

## 8. Amazon Comprehend

### What It Is

A **managed NLP service** for text analysis — sentiment, entities, key phrases, language, and **PII detection**.

### Why We Use It

- **Pre-trained PII detection** for 12+ entity types (NAME, SSN, ADDRESS, DOB, etc.)
- **No model training needed** — works out of the box
- **Per-character pricing** — cost-effective for variable workloads
- **HIPAA eligible** — safe for processing protected health information

### How We Use It

```python
response = comprehend.detect_pii_entities(Text=text, LanguageCode="en")
# Returns: [{"Type": "SSN", "BeginOffset": 10, "EndOffset": 21, "Score": 0.9998}]
```

### Interview Q&A

**Q: Why Comprehend + Regex + SageMaker instead of just Comprehend?**

> **Defense-in-depth.** Comprehend catches standard PII (SSN, CC, names) but misses insurance-specific patterns:
>
> - `POL-202456` (policy numbers) — Comprehend sees this as a random string
> - `CLM-789012` (claim numbers) — specific to our domain
> - Custom date formats in insurance forms
>
> Our regex layer catches domain patterns. SageMaker NER catches edge cases when fine-tuned on labeled insurance data.

**Q: What's the Comprehend input size limit?**

> 100KB per API call. We handle larger messages by **chunking into 90KB segments** and processing each chunk separately.

---

## 9. Amazon SageMaker

### What It Is

A **fully managed ML platform** for building, training, and deploying ML models. We use **SageMaker Inference Endpoints** for real-time predictions.

### Why We Use It

- **Custom models** — fine-tuned NER for insurance PII, fine-tuned classifier for intent
- **Serverless Inference** — pay per invocation, scales to zero
- **A/B testing** — canary deployments for model updates
- **MLOps** — Model Registry, Pipeline for retraining

### Our Endpoints

| Endpoint                      | Model                 | Purpose                          | Inference Type |
| ----------------------------- | --------------------- | -------------------------------- | -------------- |
| `insurance-pii-ner`           | Fine-tuned BERT NER   | Insurance-specific PII detection | Serverless     |
| `insurance-intent-classifier` | Fine-tuned DistilBERT | 4-class intent classification    | Serverless     |

### Interview Q&A

**Q: Why SageMaker endpoints over Bedrock for classification?**

> Bedrock Claude works as a **zero-shot** classifier (~85% accuracy). But for production, a fine-tuned DistilBERT on SageMaker achieves ~96% accuracy on our intent classes. It's also **10x cheaper** per inference ($0.0001 vs $0.001) and **5x faster** (50ms vs 250ms).

**Q: How do you retrain models?**

> 1. Approved ticket responses (from HITL) are exported to `s3://finetuning-data/`
> 2. Weekly SageMaker Pipeline retrains on new labeled data
> 3. New model version registered in **Model Registry**
> 4. **Shadow deployment** — new model runs alongside production for 48h
> 5. If metrics improve → promote to production via CDK pipeline

---

## 10. Amazon SNS

### What It Is

**Simple Notification Service** — a pub/sub messaging service for decoupling microservices, distributing events, and sending notifications.

### Why We Use It

- **Fan-out** — one email event can trigger multiple downstream consumers
- **Message filtering** — subscribers receive only relevant messages
- **SQS integration** — reliable delivery to HITL queue
- **Cross-service** — connects SES, Lambda, SQS seamlessly

### Our Topics

| Topic                 | Publishers            | Subscribers             |
| --------------------- | --------------------- | ----------------------- |
| `ingestion-topic`     | SES Receipt Rule      | Email Handler Lambda    |
| `orchestration-topic` | Email/Webhook Handler | Step Functions          |
| `hitl-review-topic`   | Step Functions        | Dashboard notifications |

### Interview Q&A

**Q: SNS vs EventBridge — why not EventBridge?**

> EventBridge is better for complex event routing with content-based filtering rules. Our routing is simple (email → pipeline), so SNS's lower latency and native SES integration wins. If we added 10+ event types, we'd migrate to EventBridge.

---

## 11. Amazon SQS

### What It Is

**Simple Queue Service** — a fully managed message queue for decoupling producers and consumers with guaranteed delivery.

### Why We Use It

- **HITL queue** — holds tickets awaiting human review with configurable visibility timeout
- **Dead Letter Queue (DLQ)** — captures failed messages for investigation
- **At-least-once delivery** — no messages lost
- **Step Functions integration** — native `waitForTaskToken` support

### Our Queues

| Queue               | Purpose                    | Retention | Visibility Timeout |
| ------------------- | -------------------------- | --------- | ------------------ |
| `hitl-review-queue` | Tickets for human review   | 14 days   | 30 min             |
| `hitl-review-dlq`   | Failed review callbacks    | 14 days   | —                  |
| `pipeline-dlq`      | Failed pipeline executions | 14 days   | —                  |

### Interview Q&A

**Q: SQS Standard vs FIFO — which and why?**

> **Standard.** HITL reviews don't need strict ordering. Standard provides higher throughput (unlimited TPS vs 3,000 for FIFO) and lower cost. If two reviewers pick up tickets simultaneously, our idempotent DynamoDB writes handle it safely.

**Q: How do you prevent poison messages?**

> After 3 failed processing attempts (`maxReceiveCount: 3`), SQS automatically moves the message to the DLQ. CloudWatch alarm triggers when DLQ depth > 0, alerting the ops team.

---

## 12. Amazon API Gateway

### What It Is

A **managed REST/WebSocket API** service that acts as the front door for applications to access backend services.

### Why We Use It

- **REST API** for webhook ingestion (WhatsApp, web chat)
- **REST API** for HITL callback endpoint
- **Built-in throttling** — protects backend from abuse
- **API keys** — authenticate external channel integrations
- **WAF integration** — block malicious requests

### Our APIs

| Endpoint                 | Method            | Lambda            | Auth        |
| ------------------------ | ----------------- | ----------------- | ----------- |
| `POST /v1/tickets`       | Create ticket     | `webhook_handler` | API Key     |
| `POST /v1/hitl/callback` | Review decision   | `hitl_callback`   | Cognito JWT |
| `GET /v1/tickets/{id}`   | Get ticket status | `ticket_status`   | API Key     |

### Interview Q&A

**Q: How do you handle rate limiting?**

> API Gateway provides **usage plans** with throttle settings:
>
> - Per-API key: 100 requests/second, burst to 200
> - Per-method: POST /tickets limited to 50/sec
> - Global: 10,000 requests/second across all endpoints
>   Exceeding limits returns `429 Too Many Requests`.

---

## 13. Amazon Cognito

### What It Is

A **managed identity service** that provides authentication, authorization, and user management for web/mobile applications.

### Why We Use It

- **HITL dashboard auth** — reviewer must be authenticated
- **MFA required** — prevents unauthorized access to sensitive ticket data
- **RBAC via groups** — reviewer, admin, read-only roles
- **JWT tokens** — stateless authentication for API calls

### Our Configuration

| Setting         | Value                                 |
| --------------- | ------------------------------------- |
| MFA             | Required (TOTP/SMS)                   |
| Password policy | 12+ chars, uppercase, number, special |
| Token expiry    | Access: 1h, Refresh: 30d              |
| Groups          | `reviewers`, `admins`, `read-only`    |

---

## 14. AWS KMS

### What It Is

**Key Management Service** — managed encryption key creation and control. Integrates with nearly every AWS service for encryption at rest and in transit.

### Why We Use It

- **Customer-managed key (CMK)** — we control key rotation and access
- **Encrypt PII mapping** in DynamoDB — most sensitive data in the system
- **S3 SSE-KMS** — audit logs encrypted with our key, not AWS default
- **Annual auto-rotation** — new key material every 365 days

### Interview Q&A

**Q: KMS vs SSE-S3 — why pay for KMS?**

> SSE-S3 uses AWS-managed keys we can't audit or restrict. With KMS, we get:
>
> - **Key policy control** — only specific IAM roles can decrypt
> - **CloudTrail logging** — every decrypt call is logged
> - **Compliance** — auditors can verify who accessed what, when

---

## 15. Amazon VPC

### What It Is

**Virtual Private Cloud** — an isolated virtual network where you launch AWS resources with full control over IP addressing, subnets, routing, and security.

### Our Network Design

```
VPC: 10.0.0.0/16
├── Public Subnet (10.0.1.0/24, 10.0.2.0/24)
│   ├── NAT Gateway (outbound internet for Lambda)
│   └── Application Load Balancer (dashboard)
├── Private Subnet (10.0.10.0/24, 10.0.11.0/24)
│   └── All 8 Lambda functions
└── Isolated Subnet (10.0.20.0/24, 10.0.21.0/24)
    └── OpenSearch Serverless VPC Endpoint
```

### VPC Endpoints (8 Total)

| Endpoint          | Service           | Type      | Why                         |
| ----------------- | ----------------- | --------- | --------------------------- |
| `vpce-s3`         | S3                | Gateway   | Free, no NAT needed         |
| `vpce-dynamodb`   | DynamoDB          | Gateway   | Free, no NAT needed         |
| `vpce-bedrock`    | Bedrock Runtime   | Interface | Keep LLM calls private      |
| `vpce-comprehend` | Comprehend        | Interface | PII detection stays private |
| `vpce-sagemaker`  | SageMaker Runtime | Interface | Custom model calls private  |
| `vpce-sfn`        | Step Functions    | Interface | Orchestration private       |
| `vpce-sqs`        | SQS               | Interface | HITL queue private          |
| `vpce-ses`        | SES               | Interface | Email sending private       |

### Interview Q&A

**Q: Why VPC Endpoints instead of NAT Gateway for all traffic?**

> **Cost.** NAT Gateway costs $0.045/GB of data processed. With ~1GB/day of Bedrock/Comprehend traffic, that's ~$1.35/day = $40/month just for NAT. VPC Interface Endpoints cost $0.01/hour (~$7/month each) but **zero data processing charges**. For S3/DynamoDB, Gateway Endpoints are **free**.

---

## 16. Amazon Textract

### What It Is

An **ML-powered document analysis** service that extracts text, tables, and forms from scanned documents and images.

### Why We Use It

- **OCR for scanned PDFs** — insurance claim forms are often scanned
- **Table extraction** — preserves structure of tabular data in forms
- **Forms extraction** — key-value pairs from standardized insurance forms
- **No model training** — works out of the box on any document

### Interview Q&A

**Q: When do you use Textract vs simple PDF text extraction?**

> If the PDF has embedded text (digitally created) → `PyPDF2` text extraction (free, fast). If the PDF is a scanned image or contains handwriting → Textract OCR ($1.50 per 1,000 pages). We check `len(pdf_text.strip()) > 50` — if too short, it's likely scanned and we fall back to Textract.

---

## 17. AWS CDK

### What It Is

**Cloud Development Kit** — an open-source IaC framework that lets you define cloud resources using programming languages (Python, TypeScript, Java) instead of YAML/JSON.

### Why We Use It Over Terraform

| Factor           | CDK (Python)            | Terraform        | CloudFormation YAML |
| ---------------- | ----------------------- | ---------------- | ------------------- |
| Language         | Python ✅ (same as app) | HCL              | YAML                |
| Type safety      | ✅ IDE autocomplete     | ⚠️ Limited       | ❌                  |
| L2 Constructs    | ✅ Sensible defaults    | ❌               | ❌                  |
| State management | ✅ AWS-managed (CFN)    | ⚠️ tf state file | ✅ AWS-managed      |
| Multi-cloud      | ❌ AWS only             | ✅               | ❌                  |
| Testing          | ✅ pytest assertions    | ✅ terratest     | ❌                  |

### Our 7 Stacks

```
NetworkStack → SecurityStack → StorageStack → SearchStack
                                    ↓              ↓
                              IngestionStack    MLStack
                                    ↓              ↓
                              OrchestrationStack
```

---

## 18. Amazon CloudWatch

### What It Is

A **monitoring and observability** service for AWS resources — collects logs, metrics, and traces. Sets alarms and triggers automated actions.

### Key Metrics We Monitor

| Metric                  | Alarm Threshold      | Action                        |
| ----------------------- | -------------------- | ----------------------------- |
| Lambda errors           | > 5% error rate      | SNS → PagerDuty               |
| Step Functions failures | > 1 failed execution | SNS → ops team                |
| DLQ depth               | > 0 messages         | SNS → immediate investigation |
| Bedrock latency         | p99 > 10s            | Scale provisioned throughput  |
| HITL queue age          | > 4h oldest message  | SNS → escalation to managers  |
| PII redaction count     | Log metric           | Dashboard tracking            |

### Interview Q&A

**Q: How do you detect if the LLM starts hallucinating in production?**

> 1. **Guardrail violation rate** — CloudWatch metric from Layer 5 checks. If hallucination rate spikes > 5%, alarm triggers model review.
> 2. **Confidence score distribution** — track p50/p95/p99 of LLM confidence. Sudden drops indicate model degradation.
> 3. **HITL rejection rate** — if human reviewers reject > 20% of AI drafts, model quality is declining.

---

## Service Cost Summary

| Service                | Estimated Monthly Cost | Cost Driver               |
| ---------------------- | ---------------------- | ------------------------- |
| Lambda                 | $15-30                 | Invocations × duration    |
| Step Functions         | $5-10                  | State transitions         |
| Bedrock (Claude)       | $100-300               | Input/output tokens       |
| Bedrock (Titan)        | $5-10                  | Embedding tokens          |
| SES                    | $1-5                   | Emails sent/received      |
| S3                     | $5-15                  | Storage + requests        |
| DynamoDB               | $10-25                 | Read/write capacity units |
| OpenSearch Serverless  | $50-100                | Minimum 2 OCUs            |
| Comprehend             | $10-30                 | Characters processed      |
| SageMaker (Serverless) | $20-50                 | Inference requests        |
| API Gateway            | $3-5                   | API calls                 |
| VPC Endpoints          | $50-60                 | 8 endpoints × $7/month    |
| KMS                    | $1-3                   | Key + API calls           |
| CloudWatch             | $5-10                  | Logs + metrics            |
| **Total**              | **~$280-650/month**    | Scales with ticket volume |

---

> **Key Takeaway:** Every service was chosen for a specific reason — serverless operations, HIPAA compliance, data residency, or cost optimization. In an interview, always lead with the **WHY** before the **WHAT**.
