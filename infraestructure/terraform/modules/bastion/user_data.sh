#!/bin/bash
set -euxo pipefail

dnf update -y
dnf install -y postgresql17 jq

# SSM agent is preinstalled on AL2023, but ensure it is running.
systemctl enable amazon-ssm-agent
systemctl restart amazon-ssm-agent
