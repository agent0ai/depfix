# Real dependency conflict: AWS CLI and Boto3

This example loads two packages whose Botocore requirements cannot be satisfied in one conventional Python environment:

```text
Package A: awscli==1.32.0  -> botocore==1.34.0
Package B: boto3==1.36.0   -> botocore>=1.36.0,<1.37.0
Shared C:  botocore
```

Those constraints have no overlap. Depfix resolves a separate dependency realm for each root, imports both packages in
one process, constructs an AWS CLI driver and a Boto3 session, and proves that each object uses its own Botocore module.
The script disables EC2 metadata lookup and makes no AWS service call, so it needs no credentials.

From the repository root:

```bash
python -m pip install -e .
python examples/conflicting_botocore_versions/application.py
```

The first run resolves and caches both graphs. Later runs reuse the exact cached artifacts.
