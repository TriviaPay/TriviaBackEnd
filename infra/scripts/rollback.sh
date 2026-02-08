#!/bin/bash
set -euo pipefail

POOL_ID="${1:?instance pool id required}"
COMPARTMENT_ID="${2:?compartment ocid required}"
INSTANCE_CONFIG_ID="${3:?instance configuration id required}"
SLEEP_SECONDS="${4:-60}"
SURGE="${5:-1}"

oci compute-management instance-pool update \
  --instance-pool-id "${POOL_ID}" \
  --instance-configuration-id "${INSTANCE_CONFIG_ID}"

infra/scripts/rolling_update.sh "${POOL_ID}" "${COMPARTMENT_ID}" "${SLEEP_SECONDS}" "${SURGE}"
