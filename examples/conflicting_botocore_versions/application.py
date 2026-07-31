import os
import sys

from packaging.version import Version

import depfix

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

# Resolve package A and B as one application selection. Their mutually
# exclusive Botocore dependencies remain bound to separate package realms.
with depfix.using("awscli==1.32.0", "boto3==1.36.0"):
    import awscli.clidriver as awscli_driver
    import boto3

awscli_botocore = awscli_driver.botocore
boto3_botocore = boto3.session.botocore

assert awscli_botocore.__depfix_version__ == "1.34.0"
assert Version("1.36.0") <= Version(boto3_botocore.__depfix_version__) < Version("1.37.0")
assert awscli_botocore is not boto3_botocore
assert awscli_botocore.__name__ != boto3_botocore.__name__
assert "botocore" not in sys.modules

# Exercise both imported SDKs without credentials or network requests.
cli = awscli_driver.CLIDriver()
session = boto3.Session(region_name="us-east-1")

assert cli.session.__class__.__module__.startswith(awscli_botocore.__name__)
assert session._session.__class__.__module__.startswith(boto3_botocore.__name__)

print(f"A  awscli=={awscli_driver.__depfix_version__} -> botocore=={awscli_botocore.__depfix_version__}")
print(f"B  boto3=={boto3.__depfix_version__} -> botocore=={boto3_botocore.__depfix_version__}")
print(f"C1 {awscli_botocore.__name__}")
print(f"C2 {boto3_botocore.__name__}")
print("PASS: incompatible Botocore versions are isolated in one Python process")
