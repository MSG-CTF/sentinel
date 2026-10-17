"""Run in authenticated AWS CloudShell to create the dedicated Sentinel stack."""

import argparse
import json
from pathlib import Path
import subprocess

REGION = "ap-northeast-2"
STACK = "msg-sentinel"


def aws(*args, region=REGION):
    result = subprocess.run(["aws", *args, "--region", region, "--output", "json", "--no-cli-pager"],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def provision(template):
    identity = aws("sts", "get-caller-identity")
    plan = aws("freetier", "get-account-plan-state", region="us-east-1")
    if (plan.get("accountPlanType") != "FREE" or plan.get("accountPlanStatus") != "ACTIVE"
            or float(plan.get("accountPlanRemainingCredits", {}).get("amount", 0)) <= 0):
        raise RuntimeError("An active Free plan with remaining credits is required")
    vpcs = aws("ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true")["Vpcs"]
    if len(vpcs) != 1:
        raise RuntimeError("Choose a public subnet explicitly; one default VPC was not found")
    vpc = vpcs[0]["VpcId"]
    subnets = aws("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=" + vpc,
                  "Name=default-for-az,Values=true")["Subnets"]
    public = sorted((s for s in subnets if s["MapPublicIpOnLaunch"] and s["AvailableIpAddressCount"] > 0),
                    key=lambda s: s["AvailabilityZone"])
    if not public:
        raise RuntimeError("No default public subnet available")
    providers = aws("iam", "list-open-id-connect-providers")["OpenIDConnectProviderList"]
    oidc = next((p["Arn"] for p in providers
                 if p["Arn"].endswith(":oidc-provider/token.actions.githubusercontent.com")), "")
    if oidc:
        existing = aws("iam", "get-open-id-connect-provider", "--open-id-connect-provider-arn", oidc)
        if "sts.amazonaws.com" not in existing["ClientIDList"]:
            raise RuntimeError("Existing GitHub OIDC provider lacks the AWS audience")
    aws("cloudformation", "validate-template", "--template-body", "file://" + str(template))
    aws("cloudformation", "create-stack", "--stack-name", STACK,
        "--template-body", "file://" + str(template), "--capabilities", "CAPABILITY_IAM",
        "--parameters", "ParameterKey=VpcId,ParameterValue=" + vpc,
        "ParameterKey=SubnetId,ParameterValue=" + public[0]["SubnetId"],
        "ParameterKey=ExistingOidcProviderArn,ParameterValue=" + oidc,
        "--tags", "Key=Project,Value=MSG-CTF", "Key=Service,Value=Sentinel")
    print(json.dumps({"status": "creation_started", "stack": STACK,
                      "region": REGION, "account": identity["Account"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        provision(args.template.resolve())
    except subprocess.CalledProcessError as error:
        # Provisioning commands contain resource metadata, never probe credentials.
        print(error.stderr.strip())
        raise SystemExit(1)
    except RuntimeError as error:
        print(str(error))
        raise SystemExit(1)
