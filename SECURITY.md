<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# Security Policy

## Disclaimer

This project is provided as sample/educational code and is NOT intended for production use without additional security hardening. See "Production Hardening Recommendations" below.

## Reporting Vulnerabilities

If you discover a security vulnerability, report it by emailing aws-security@amazon.com.
Do not report security vulnerabilities through public GitHub issues.

## AWS Services Used

- **Amazon DynamoDB** — stores test case feedback patterns for learning
- **Amazon Bedrock (Claude Sonnet 4.6)** — AI-powered code analysis and test generation
- **Amazon Cognito** — user authentication and JWT token management
- **AWS Lambda API** — reads Lambda function code for analysis (read-only)
- **Amazon CloudWatch Logs** — application logging for Amazon Bedrock AgentCore runtime

## Known Security Considerations

| Item | Category | Status | Rationale |
|------|----------|--------|-----------|
| Lambda read policy uses `function:*` | Security Debt | Documented | Tool needs to read any user-specified Lambda for code analysis. Scoped to account and region. See SD-7 below. |
| DynamoDB uses AWS-managed encryption | Security Debt | Accepted | Adequate for non-sensitive test case pattern data. See SD-1 below. |
| Cognito MFA set to OPTIONAL | Security Debt | Accepted | Reduces friction for development tool. Strong password policy + AdvancedSecurityMode compensate. See SD-2 below. |
| CloudWatch Logs not encrypted with KMS | Security Debt | Accepted | Logs contain operational data only, source code sanitized. See SD-3 below. |
| Non-localhost deployment requires TLS | Deployment Concern | Documented | Streamlit runs on localhost (secure). Remote deployments require TLS-terminating reverse proxy. See SD-6 below. |
| No Bedrock API timeout/retry config | Security Debt | Accepted | boto3 defaults sufficient for development. Production recommendations provided. See SD-9 below. |
| Validator lacks input schema validation | Security Debt | Accepted | Multiple validation layers exist. Test cases are system-generated. See SD-10 below. |
| Lambda code sent to Bedrock | Security Debt | Accepted | Core functionality. Bedrock doesn't use data for training. TLS encrypted. See SD-11 below. |

## Production Hardening Recommendations

### Security Controls Implemented

The following security controls are in place:
- Amazon Bedrock Guardrail on all model invocations (prompt attack, content, PII, denied topics)
- Prompt injection protection with input sanitization
- Server-side token storage (never in URLs)
- Source code sanitization before API responses
- Comprehensive input validation on all API inputs
- Rate limiting (5 requests per 60 seconds per user)
- User-friendly error messages (internal details removed)
- Mandatory Cognito authentication (admin-only user creation)
- File size and compression ratio validation
- SPDX license identifiers on all source files
- Bedrock response structure validation
- SHA-256 hashing for test case deduplication

### Additional Production Hardening

- **IAM**: Scope Lambda read access using resource conditions or tags (see SD-7 recommendations)
- **Encryption**: Use customer-managed KMS keys for DynamoDB and CloudWatch Logs (see SD-1, SD-3)
- **Authentication**: Set Cognito MFA to REQUIRED for production (see SD-2)
- **Networking**: For non-localhost deployments, use ALB/CloudFront with TLS termination (see SD-6)
- **Logging**: Enable CloudTrail data events for DynamoDB access auditing
- **Monitoring**: Set up CloudWatch alarms for rate limit violations and unusual patterns
- **Bedrock Configuration**: Add explicit timeouts and retry logic (see SD-9)

## Accepted Security Debt

The following security considerations have been evaluated and accepted as design decisions. This section documents the rationale, mitigations, and recommendations for each.
|------|----------|--------|-----------|

### SD-1: DynamoDB Uses AWS-Managed Encryption (SSE-S3)

**Severity**: Medium  
**Status**: Accepted  
**File**: `cloudformation/complete-infrastructure.yaml` line 27

**Description**: DynamoDB table uses AWS-managed encryption (SSE-S3) instead of customer-managed KMS keys.

**Rationale**:
- Test case data is non-sensitive (input patterns, test metadata)
- No PII, credentials, or business-critical data stored
- AWS-managed encryption provides adequate protection for this use case
- Reduces operational complexity (no key rotation, no KMS costs)

**Data Stored**:
- Test input patterns (JSON structures)
- Test types (positive/negative/edge)
- Feedback status (accepted/rejected)
- Rejection reasons (predefined categories)
- Metadata (timestamps, usage counts)

**When to Use Customer-Managed KMS**:
- Storing sensitive test data (PII, credentials)
- Compliance requirements (HIPAA, PCI-DSS)
- Audit trail requirements for key usage
- Cross-account access with fine-grained control

**Migration Path**:
If customer-managed KMS is required, update CloudFormation:
```yaml
SSESpecification:
  SSEEnabled: true
  SSEType: KMS
  KMSMasterKeyId: !Ref MyKMSKey
```

---

### SD-2: Cognito MFA Set to OPTIONAL

**Severity**: Medium  
**Status**: Accepted  
**File**: `cloudformation/complete-infrastructure.yaml` line 48

**Description**: Cognito MFA is set to OPTIONAL instead of REQUIRED.

**Rationale**:
- This is a development/testing tool, not a production application
- Strong password policy enforced (8+ chars, uppercase, lowercase, numbers, symbols)
- Cognito AdvancedSecurityMode ENFORCED provides threat detection
- Optional MFA balances security with developer usability
- Users can enable MFA voluntarily

**Compensating Controls**:
1. **Strong Password Policy**:
   - Minimum 8 characters
   - Requires uppercase, lowercase, numbers, symbols
   - Temporary passwords expire in 7 days

2. **Advanced Security Mode**:
   - Detects compromised credentials
   - Adaptive authentication based on risk
   - Blocks suspicious sign-in attempts

3. **Rate Limiting**:
   - 5 requests per 60 seconds per user
   - Prevents brute force attacks

4. **Token Expiration**:
   - Access tokens: 60 minutes
   - ID tokens: 60 minutes
   - Refresh tokens: 30 days

**When to Require MFA**:
- Production deployments
- Access to sensitive Lambda functions
- Compliance requirements
- Multi-user team environments

**Migration Path**:
To require MFA, update CloudFormation:
```yaml
MfaConfiguration: ON  # Change from OPTIONAL to ON
```

---

### SD-3: CloudWatch Logs Not Encrypted with KMS

**Severity**: Medium  
**Status**: Accepted  
**File**: `cloudformation/complete-infrastructure.yaml`

**Description**: CloudWatch Logs for AgentCore runtime are not encrypted with customer-managed KMS keys.

**Rationale**:
- Logs contain operational data, not sensitive information
- Source code is sanitized before logging
- AWS-managed encryption at rest is sufficient
- Reduces operational complexity and costs

**Data Logged**:
- Request metadata (function names, user IDs, timestamps)
- Error messages (sanitized, no internal details)
- Performance metrics (execution time, chunk counts)
- Rate limit violations

**Data NOT Logged**:
- Lambda source code (sanitized before logging)
- Authentication tokens (never logged)
- AWS credentials (never logged)
- User passwords (never logged)

**When to Use KMS Encryption**:
- Compliance requirements (HIPAA, PCI-DSS)
- Logging sensitive data (not recommended)
- Cross-account log access with fine-grained control

**Migration Path**:
To enable KMS encryption, update CloudFormation:
```yaml
CloudWatchLogsAccess:
  PolicyDocument:
    Statement:
      - Effect: Allow
        Action:
          - logs:CreateLogGroup
          - logs:CreateLogStream
          - logs:PutLogEvents
          - kms:Decrypt
          - kms:GenerateDataKey
        Resource:
          - !Sub 'arn:aws:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/*'
          - !Ref MyKMSKey
```

---

### SD-6: Non-Localhost Deployment Requires TLS

**Severity**: Medium  
**Status**: Accepted (with documentation)
**File**: README.md, deployment guidance

**Description**: Streamlit UI runs on localhost by default (secure loopback). However, README links to AWS blog for deploying to AWS for team access without explicit TLS guidance.

**Current Security Posture**:
- **Localhost deployment** (default): Secure loopback interface, no network exposure
- **AWS service calls**: All boto3 calls use HTTPS/TLS automatically
- **AgentCore API**: HTTPS/TLS enforced by AWS

**Risk for Non-Localhost Deployments**:
- Browser-to-Streamlit traffic would be unencrypted over the network
- Authentication tokens could be intercepted
- Session hijacking possible

**Mitigation**:
- **REQUIRED**: Use TLS-terminating reverse proxy for non-localhost deployments
- Options:
  - Application Load Balancer (ALB) with ACM certificate
  - CloudFront with ACM certificate
  - nginx with Let's Encrypt certificate
  - API Gateway with custom domain

**Deployment Guidance**:

**For Localhost (Default)**:
```bash
streamlit run app.py  # Runs on http://localhost:8501 (secure loopback)
```

**For Remote Deployment (Team Access)**:

1. **Using Application Load Balancer**:
   ```yaml
   # Add to CloudFormation
   LoadBalancer:
     Type: AWS::ElasticLoadBalancingV2::LoadBalancer
     Properties:
       Scheme: internet-facing
   
   Listener:
     Type: AWS::ElasticLoadBalancingV2::Listener
     Properties:
       Protocol: HTTPS
       Port: 443
       Certificates:
         - CertificateArn: !Ref ACMCertificate
   ```

2. **Using CloudFront**:
   ```yaml
   Distribution:
     Type: AWS::CloudFront::Distribution
     Properties:
       ViewerCertificate:
         AcmCertificateArn: !Ref ACMCertificate
         SslSupportMethod: sni-only
         MinimumProtocolVersion: TLSv1.2_2021
   ```

3. **Using nginx**:
   ```nginx
   server {
       listen 443 ssl;
       ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
       
       location / {
           proxy_pass http://localhost:8501;
       }
   }
   ```

**Security Requirements for Remote Deployment**:
- ✅ TLS 1.2 or higher
- ✅ Valid SSL/TLS certificate (not self-signed)
- ✅ HSTS header enabled
- ✅ Secure cookie flags (Streamlit handles this)
- ✅ Network security groups restricting access

---

### SD-7: Lambda function:* Wildcard (Documented Design Decision)

**Severity**: Medium  
**Status**: Accepted (reclassified from MF-H2)  
**File**: `cloudformation/complete-infrastructure.yaml` line 150

**Description**: IAM policy grants `lambda:GetFunction` on all functions in account/region.

**Rationale**: See "Security Design Decisions" section below for full details.

**Key Points**:
- Core functionality requires analyzing any user-specified Lambda
- Scoped to account + region (not `*:*`)
- Source code sanitized before returning
- Prompt injection protection prevents extraction
- Tag-based access control available for production

---

### SD-9: No Bedrock API Timeout or Retry Configuration

**Severity**: Medium  
**Status**: Accepted (with documentation)  
**File**: `utils/code_analyzer.py`, `agents/generator_agent.py`

**Description**: Bedrock `converse()` calls have no explicit timeout and no `ThrottlingException` handling with backoff. boto3 has default retries but no explicit timeout configuration.

**Current Behavior**:
- boto3 uses default retry configuration (3 attempts with exponential backoff)
- No explicit timeout set (uses boto3 defaults)
- No explicit `ThrottlingException` handling

**Risk**:
- Long-running requests could hang indefinitely
- Throttling errors could fail without retry
- No control over timeout behavior

**Rationale for Acceptance**:
- boto3 default retry behavior is generally sufficient for development/testing
- Bedrock API is highly available with good default behavior
- Adding explicit configuration adds complexity
- Can be configured at deployment time via environment

**Production Recommendations**:

1. **Configure explicit timeouts and retries**:
   ```python
   from botocore.config import Config
   
   bedrock_config = Config(
       read_timeout=30,  # 30 second read timeout
       connect_timeout=10,  # 10 second connect timeout
       retries={
           'max_attempts': 3,
           'mode': 'adaptive'  # Adaptive retry mode
       }
   )
   
   bedrock_client = boto3.client(
       'bedrock-runtime',
       region_name='us-east-1',
       config=bedrock_config
   )
   ```

2. **Add explicit throttling handling**:
   ```python
   from botocore.exceptions import ClientError
   import time
   
   max_retries = 3
   for attempt in range(max_retries):
       try:
           response = bedrock_client.converse(...)
           break
       except ClientError as e:
           if e.response['Error']['Code'] == 'ThrottlingException':
               if attempt < max_retries - 1:
                   wait_time = (2 ** attempt) + random.uniform(0, 1)
                   logger.warning(f"Throttled, retrying in {wait_time:.2f}s")
                   time.sleep(wait_time)
               else:
                   raise
           else:
               raise
   ```

3. **Monitor Bedrock API metrics**:
   - Track invocation latency
   - Monitor throttling errors
   - Set up CloudWatch alarms for high error rates

**Why Not Implemented**:
- Development/testing tool where default behavior is acceptable
- boto3 defaults are reasonable for most use cases
- Can be configured externally without code changes
- Adds complexity for minimal benefit in target environment

**Mitigation**:
- boto3 default retries handle transient failures
- Rate limiting at application level (5 requests/60s) reduces throttling risk
- Bedrock has high availability and good default behavior

---

### SD-10: Validator process_user_feedback Lacks Input Schema Validation

**Severity**: Medium  
**Status**: Accepted (with documentation)  
**File**: `agents/validator_agent.py` lines 165-170

**Description**: The `process_user_feedback()` method accepts arbitrary dicts without validating structure, field types, or list size limits. Downstream `memory_store` validates `feedback` and `rejection_reason` against allowlists, but the validator doesn't check `test_case` structure or limit batch size.

**Current Validation**:
- `memory_store.save_feedback()` validates:
  - `feedback` field: must be 'accepted' or 'rejected'
  - `rejection_reason`: must be in predefined allowlist
  - Pattern hash for deduplication
- AgentCore handler validates:
  - `function_name`: length, pattern
  - `test_cases_with_feedback`: type, max 100 items
  - `user_id`: type, max 200 chars

**Missing Validation**:
- `test_case` structure within each feedback item
- Field types within `test_case` (input_event, test_type, etc.)
- Size of individual `test_case` objects
- Nested field validation

**Risk**:
- Malformed test_case could cause errors in memory_store
- Large test_case objects could consume memory
- Invalid field types could cause downstream errors

**Rationale for Acceptance**:
- Defense in depth: validation at multiple layers (handler, validator, memory_store)
- Handler already validates batch size (max 100 items)
- Memory store validates critical fields (feedback, rejection_reason)
- Test cases are generated by the system itself (not arbitrary user input)
- Feedback flow is authenticated and rate-limited

**Compensating Controls**:
1. **Handler-level validation** (MF-H1 fix):
   - Validates `test_cases_with_feedback` is a list
   - Limits to 100 items maximum
   - Validates `user_id` and `function_name`

2. **Memory store validation**:
   - Validates `feedback` against allowlist
   - Validates `rejection_reason` against allowlist
   - Pattern hash prevents duplicates

3. **Rate limiting**:
   - 5 requests per 60 seconds per user
   - Prevents abuse even with malformed data

4. **Authentication**:
   - Cognito JWT required
   - User identified for audit trail

**Production Recommendations**:

If stricter validation is needed, add to `validator_agent.py`:

```python
def _validate_test_case_structure(self, test_case: Dict[str, Any]) -> bool:
    """Validate test case structure."""
    required_fields = ['test_type', 'input_event']
    
    # Check required fields
    for field in required_fields:
        if field not in test_case:
            logger.warning(f"Test case missing required field: {field}")
            return False
    
    # Validate test_type
    if test_case['test_type'] not in ['positive', 'negative', 'edge']:
        logger.warning(f"Invalid test_type: {test_case['test_type']}")
        return False
    
    # Validate input_event is dict
    if not isinstance(test_case['input_event'], dict):
        logger.warning("input_event is not a dict")
        return False
    
    # Size limit
    test_case_json = json.dumps(test_case)
    if len(test_case_json) > 10000:  # 10KB per test case
        logger.warning(f"Test case too large: {len(test_case_json)} bytes")
        return False
    
    return True
```

**Why Not Implemented**:
- Test cases are system-generated, not arbitrary user input
- Multiple validation layers already exist
- Would add complexity with minimal security benefit
- Current validation is sufficient for intended use case

---

### SD-11: Lambda Source Code Sent to Bedrock Without Data Handling Documentation

**Severity**: Medium  
**Status**: Accepted (with documentation)  
**File**: `agents/generator_agent.py`, `agents/analyzer_agent.py`

**Description**: Lambda source code (potentially containing business logic or sensitive patterns) is sent to Amazon Bedrock for analysis without documented data handling procedures.

**Current Security Posture**:

1. **Transport Security**:
   - boto3 uses HTTPS/TLS by default for all AWS API calls
   - All data in transit is encrypted
   - Certificate validation enforced

2. **Amazon Bedrock Data Handling**:
   - Amazon Bedrock does NOT use customer data for model training
   - Prompts and responses are not stored by Amazon Bedrock
   - Data is processed in-memory and discarded after response
   - See: [Amazon Bedrock Data Protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)

3. **Data Minimization**:
   - Source code is chunked (max 20,000 chars per chunk)
   - Only code files analyzed (dependencies filtered out)
   - Source code sanitized before returning to users
   - Only patterns/metadata returned, not full code

**What Data is Sent to Bedrock**:
- Lambda function source code (Python, Node.js, Java, etc.)
- Input/output patterns extracted from code
- Error handling patterns
- Rejection patterns from previous feedback

**What Data is NOT Sent**:
- AWS credentials or secrets
- Environment variables (filtered out)
- Binary files or compiled code
- Dependency libraries (node_modules, venv, etc.)
- Test files (if in ignore patterns)

**Risk Assessment**:

**Low Risk**:
- Amazon Bedrock does not retain customer data
- Data encrypted in transit (TLS)
- No data used for model training
- AWS compliance certifications (SOC, ISO, PCI-DSS)

**Medium Risk**:
- Business logic patterns visible to Bedrock service
- Proprietary algorithms could be analyzed
- Code structure and patterns exposed

**Mitigation**:
1. **Use in appropriate environments**:
   - Development and testing environments: ✅ Acceptable
   - Production environments: ⚠️ Evaluate based on data sensitivity
   - Highly regulated environments: ❌ May require additional controls

2. **Data classification**:
   - Public/internal code: ✅ Acceptable
   - Confidential business logic: ⚠️ Evaluate risk
   - Regulated data (PII, PHI, PCI): ❌ Do not analyze

3. **Alternative approaches for sensitive code**:
   - Use in isolated AWS account
   - Analyze only non-sensitive functions
   - Use target filters to exclude sensitive code
   - Deploy in VPC with PrivateLink (if available)

**Compliance Considerations**:

**GDPR/Privacy**:
- Ensure Lambda code does not contain PII
- If code contains PII, consider data residency requirements
- Amazon Bedrock available in multiple regions

**HIPAA**:
- Amazon Bedrock is HIPAA eligible
- Sign BAA with AWS if analyzing healthcare-related code
- Ensure Lambda code does not contain PHI

**PCI-DSS**:
- Do not analyze Lambda functions that process cardholder data
- Ensure code does not contain payment card information

**SOC 2**:
- Amazon Bedrock has SOC 2 Type II certification
- Suitable for most SOC 2 environments

**Documentation for Users**:

Add to README or deployment guide:

```markdown
## Data Handling and Privacy

### What Data is Sent to Amazon Bedrock

This tool sends Lambda function source code to Amazon Bedrock for AI-powered analysis. 

**Data Sent**:
- Lambda function source code
- Code patterns and structure
- Input/output patterns

**Data NOT Sent**:
- AWS credentials or secrets
- Environment variables
- Binary files or dependencies

### Amazon Bedrock Data Handling

- Amazon Bedrock does NOT use your data for model training
- Prompts and responses are NOT stored
- Data is encrypted in transit (TLS)
- Data is processed in-memory and discarded

### Recommendations

**Safe to Use**:
- Development and testing environments
- Non-sensitive business logic
- Public or internal code

**Evaluate Before Use**:
- Production environments
- Proprietary algorithms
- Confidential business logic

**Do Not Use**:
- Code containing PII, PHI, or PCI data
- Highly regulated environments without approval
- Code with trade secrets or patents

### Compliance

Amazon Bedrock is compliant with:
- SOC 1, 2, 3
- ISO 27001, 27017, 27018
- PCI-DSS
- HIPAA (with BAA)

For more information, see:
- [Amazon Bedrock Data Protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- [AWS Compliance Programs](https://aws.amazon.com/compliance/programs/)
```

**Why Accepted as Security Debt**:
- Core functionality requires sending code to Bedrock
- Amazon Bedrock has strong data protection guarantees
- Risk is acceptable for development/testing use case
- Users can make informed decisions based on documentation
- Alternative approaches (local analysis) would require significant rearchitecture

---

## Security Architecture

This document describes the security design decisions and threat model for the Lambda Test Case Generator.

## Threat Model

### In Scope
- Unauthorized access to Lambda source code
- Prompt injection attacks
- Input validation bypasses
- Authentication token leakage
- Resource exhaustion attacks

### Out of Scope
- Physical access to AWS infrastructure
- Compromise of AWS IAM credentials
- Social engineering attacks
- DDoS attacks on AWS services

## Security Design Decisions

### 1. Broad Lambda Read Access (Security Debt)

**Decision**: The AgentCore execution role has `lambda:GetFunction` and `lambda:GetFunctionConfiguration` permissions on all Lambda functions in the account (`arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:*`).

**Rationale**: 
- This is the core functionality of the tool - analyzing any user-specified Lambda function
- Users explicitly provide the function name they want to analyze
- The tool is designed for development/testing environments where developers need to analyze their own functions

**Risk**: 
- If the AgentCore endpoint is compromised, an attacker could potentially analyze any Lambda function in the account
- Combined with a prompt injection vulnerability, this could lead to source code exposure

**Mitigations**:
1. **Source Code Sanitization**: All source code is removed from API responses before returning to users (see `sanitize_analysis_result()` in `main.py`)
2. **Prompt Injection Protection**: Custom instructions are sanitized to prevent attempts to extract source code (see `_sanitize_custom_instructions()` in `agents/generator_agent.py`)
3. **Authentication**: Cognito JWT validation on every AgentCore request
4. **Input Validation**: All inputs validated before processing
5. **Scoped to Account**: Permissions limited to same account and region (not `*:*`)
6. **Optional IAM Conditions**: Organizations can add conditions to further restrict access (see below)

**Scope Limitations**:
- Region: `${AWS::Region}` (same region as deployment)
- Account: `${AWS::AccountId}` (same account only)
- Resource: `function:*` (all functions, but scoped by above)

**Alternative Approaches Considered**:

1. **Tag-Based Access Control** (Recommended for Production):
   ```yaml
   Condition:
     StringEquals:
       'aws:ResourceTag/AllowTestGeneration': 'true'
   ```
   - Pros: Fine-grained control, explicit opt-in per function
   - Cons: Requires tagging all Lambda functions, operational overhead
   - **Recommendation**: Use this in production environments

2. **Naming Pattern Restrictions**:
   ```yaml
   Resource: 
     - !Sub 'arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:dev-*'
     - !Sub 'arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:test-*'
   ```
   - Pros: Simple, based on naming conventions
   - Cons: Requires consistent naming, easy to bypass
   - **Recommendation**: Use for development environments

3. **Explicit Function List**:
   ```yaml
   Resource:
     - !Sub 'arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:function1'
     - !Sub 'arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:function2'
   ```
   - Pros: Maximum control
   - Cons: High maintenance, not scalable
   - **Recommendation**: Not practical for this use case

**Implementation Guidance**:

For **Development/Testing** environments:
- Use the default `function:*` policy (current implementation)
- Rely on source code sanitization and prompt injection protection
- Ensure Cognito authentication is properly configured

For **Production** environments:
- Add tag-based conditions to the IAM policy
- Tag only approved Lambda functions with `AllowTestGeneration: true`
- Monitor CloudTrail logs for `lambda:GetFunction` calls
- Consider deploying in a separate AWS account

**Monitoring**:
- Enable CloudTrail logging for Lambda API calls
- Set up CloudWatch alarms for unusual `lambda:GetFunction` patterns
- Review IAM Access Analyzer findings regularly

### 2. Source Code Protection

**Decision**: Lambda source code is analyzed internally but never returned to users.

**Implementation**:
- `sanitize_analysis_result()` removes all code chunks before returning results
- Only metadata (patterns, inputs, outputs) is exposed
- Bedrock prompts include explicit rules against outputting source code

**Verification**:
- Review `main.py` lines 40-90 for sanitization logic
- Test by attempting to extract code via custom instructions
- Monitor API responses for code leakage

### 3. Prompt Injection Protection

**Decision**: All custom instructions are sanitized before being sent to Bedrock.

**Implementation**:
- `_sanitize_custom_instructions()` filters malicious patterns
- Removes attempts to override system instructions
- Limits length to prevent token exhaustion
- Bedrock prompts include security rules that cannot be overridden

**Patterns Filtered**:
- "ignore previous instructions"
- "output the full source code"
- "reveal the Lambda function"
- System command patterns (`${...}`, backticks)

### 4. Input Validation

**Decision**: All AgentCore API inputs are validated before processing.

**Implementation**:
- Type checking for all parameters
- Length limits on strings
- Character whitelisting with regex
- Range validation for numbers
- Defense in depth: validation at handler and function levels

**Limits**:
- function_name: 170 chars, alphanumeric + `_:/-`
- num_test_cases: 1-50
- custom_instructions: 2000 chars
- target_filter: 200 chars
- ignore_patterns: 50 patterns max

### 5. Authentication & Authorization

**Decision**: Cognito JWT authentication required for all AgentCore requests.

**Implementation**:
- JWT tokens validated on every request
- Tokens stored server-side (Streamlit) or in secure credential stores (API clients)
- Token refresh without exposing credentials
- Session expires on browser close (Streamlit)

## Security Best Practices

### For Administrators

1. **Use Tag-Based Access Control in Production**:
   ```bash
   # Tag Lambda functions that can be analyzed
   aws lambda tag-resource \
     --resource arn:aws:lambda:us-east-1:123456789012:function:my-function \
     --tags AllowTestGeneration=true
   ```

2. **Enable CloudTrail Logging**:
   ```bash
   aws cloudtrail create-trail \
     --name lambda-test-generator-audit \
     --s3-bucket-name my-audit-bucket
   ```

3. **Monitor with CloudWatch**:
   ```bash
   # Create alarm for excessive Lambda GetFunction calls
   aws cloudwatch put-metric-alarm \
     --alarm-name lambda-getfunction-spike \
     --metric-name CallCount \
     --namespace AWS/Lambda \
     --statistic Sum \
     --period 300 \
     --threshold 100 \
     --comparison-operator GreaterThanThreshold
   ```

4. **Use Separate AWS Accounts**:
   - Deploy test generator in a separate "tools" account
   - Use cross-account IAM roles with explicit trust policies
   - Limit blast radius of potential compromise

5. **Regular Security Reviews**:
   - Review IAM Access Analyzer findings monthly
   - Audit CloudTrail logs for suspicious activity
   - Update dependencies regularly (`pip install --upgrade`)

### For Developers

1. **Never Hardcode Credentials**:
   - Use IAM roles for EC2/ECS
   - Use environment variables for local development
   - Rotate access keys regularly

2. **Validate All Inputs**:
   - Even if using the Streamlit UI, inputs are validated server-side
   - Direct API calls have the same validation

3. **Review Generated Tests**:
   - Always review test cases before running them
   - Provide feedback to improve the system
   - Report any suspicious outputs

4. **Use MFA**:
   - Enable MFA on AWS accounts
   - Enable MFA on Cognito user accounts (optional but recommended)

## Reporting Security Issues

If you discover a security vulnerability, please report it responsibly.

**Do NOT**:
- Open a public GitHub issue
- Discuss the vulnerability publicly
- Attempt to exploit the vulnerability

**Please include**:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours and provide a timeline for fixes.

## Security Updates

This document is reviewed and updated quarterly or when significant security changes are made.

**Last Updated**: 2025-03-28
**Next Review**: 2025-06-28

## Compliance

This tool is designed for development and testing environments. For production use or regulated environments:

- Conduct a security assessment
- Implement additional controls (tag-based access, separate accounts)
- Enable comprehensive logging and monitoring
- Review with your security team

## References

- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS Lambda Security](https://docs.aws.amazon.com/lambda/latest/dg/lambda-security.html)
- [Amazon Bedrock Security](https://docs.aws.amazon.com/bedrock/latest/userguide/security.html)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

## Resource Cleanup

1. Delete the Amazon Bedrock AgentCore runtime:
   ```bash
   agentcore launch --delete
   ```

2. Delete the CloudFormation stack:
   ```bash
   aws cloudformation delete-stack --stack-name lambda-test-generator-infra --region us-east-1
   ```

3. Resources deleted with the stack:
   - Amazon DynamoDB table
   - Amazon Cognito user pool and app client
   - IAM execution role

4. Manually delete CloudWatch log groups created during runtime:
   ```bash
   aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/runtimes/lambda_test_generator-<RUNTIME_ID>-DEFAULT
   ```

## Dependencies

| Dependency | Version | Notes |
|------------|---------|-------|
| boto3 | ≥ 1.42.75 | AWS SDK — default credential chain |
| streamlit | Latest | Web UI framework — localhost only |
| requests | Latest | Used to download Lambda code from pre-signed S3 URL |
| python-dotenv | Latest | Environment variable loading |
| bedrock-agentcore-starter-toolkit | Latest | Amazon Bedrock AgentCore SDK for runtime operations |

**Security Notes**:
- All dependencies use HTTPS for network communication
- boto3 uses AWS Signature Version 4 for authentication
- No dependencies have known critical vulnerabilities (as of last update)
- Run `pip install --upgrade -r requirements.txt` regularly to get security patches
