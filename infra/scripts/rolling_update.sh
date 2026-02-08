#!/bin/bash
set -euo pipefail

POOL_ID="${1:?instance pool id required}"
COMPARTMENT_ID="${2:?compartment ocid required}"
SLEEP_SECONDS="${3:-60}"
SURGE="${4:-1}"
LB_ID="${LB_ID:-}"
BACKEND_SET_NAME="${BACKEND_SET_NAME:-}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_DRAIN_TIMEOUT_SECONDS="${BACKEND_DRAIN_TIMEOUT_SECONDS:-120}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
VNIC_SUBNET_ID="${VNIC_SUBNET_ID:-}"

list_instance_ids() {
  oci compute-management instance-pool list-instances \
    --instance-pool-id "${POOL_ID}" \
    --compartment-id "${COMPARTMENT_ID}" \
    --query "data[].id" \
    --raw-output
}

select_vnic_ip() {
  local instance_id="${1:?instance id required}"
  local vnic_query="${2:?vnic query required}"
  local vnic_info
  local ip

  vnic_info=$(oci compute instance list-vnics \
    --instance-id "${instance_id}" \
    --query "${vnic_query}.{id:id,subnet:\"subnet-id\",ip:\"private-ip\",primary:\"is-primary\"}" \
    --output json 2>/dev/null || true)
  ip=$(oci compute instance list-vnics \
    --instance-id "${instance_id}" \
    --query "${vnic_query}.\"private-ip\"" \
    --raw-output 2>/dev/null || true)

  if [ -z "${ip}" ] || [ "${ip}" = "null" ]; then
    return 1
  fi

  echo "Selected VNIC for ${instance_id}: ${vnic_info}" >&2
  echo "${ip}"
}

wait_for_backend_set_ready() {
  local expected_count="${1:-}"
  if [ -z "${LB_ID}" ] || [ -z "${BACKEND_SET_NAME}" ]; then
    echo "LB backend health check skipped (LB_ID/BACKEND_SET_NAME not set)"
    return 0
  fi

  local waited=0
  while true; do
    local unhealthy
    local count
    unhealthy=$(oci lb backend-set-health get \
      --load-balancer-id "${LB_ID}" \
      --backend-set-name "${BACKEND_SET_NAME}" \
      --query "length(data.backends[?status!='OK'])" \
      --raw-output 2>/dev/null || true)
    count=$(oci lb backend-set-health get \
      --load-balancer-id "${LB_ID}" \
      --backend-set-name "${BACKEND_SET_NAME}" \
      --query "length(data.backends)" \
      --raw-output 2>/dev/null || true)
    if [ "${unhealthy:-1}" = "0" ] && [ -n "${count}" ]; then
      if [ -z "${expected_count}" ] || [ "${count}" -ge "${expected_count}" ]; then
        echo "LB backend set healthy (${count} backends)"
        return 0
      fi
    fi
    if [ "${waited}" -ge "${MAX_WAIT_SECONDS}" ]; then
      echo "Timed out waiting for LB backend health (unhealthy=${unhealthy:-unknown}, count=${count:-unknown})"
      return 1
    fi
    sleep "${SLEEP_SECONDS}"
    waited=$((waited + SLEEP_SECONDS))
  done
}

backend_name_for_instance() {
  local instance_id="${1:?instance id required}"
  local ip

  if [ -n "${VNIC_SUBNET_ID}" ]; then
    ip=$(select_vnic_ip "${instance_id}" "data[?\"subnet-id\"=='${VNIC_SUBNET_ID}'] | [0]" || true)
    if [ -z "${ip}" ] || [ "${ip}" = "null" ]; then
      echo "No VNIC in subnet ${VNIC_SUBNET_ID} for ${instance_id}; aborting rollout" >&2
      return 1
    fi
  else
    ip=$(select_vnic_ip "${instance_id}" "data[?\"is-primary\"==\`true\`] | [0]" || true)
    if [ -z "${ip}" ] || [ "${ip}" = "null" ]; then
      return 1
    fi
  fi

  echo "${ip}:${BACKEND_PORT}"
}

wait_for_backend_member() {
  local instance_id="${1:?instance id required}"
  if [ -z "${LB_ID}" ] || [ -z "${BACKEND_SET_NAME}" ]; then
    echo "LB backend member check skipped (LB_ID/BACKEND_SET_NAME not set)"
    return 0
  fi

  local waited=0
  while true; do
    local backend_name
    backend_name=$(backend_name_for_instance "${instance_id}" || true)
    if [ -n "${backend_name}" ]; then
      local status
      status=$(oci lb backend-set-health get \
        --load-balancer-id "${LB_ID}" \
        --backend-set-name "${BACKEND_SET_NAME}" \
        --query "data.backends[?name=='${backend_name}'] | [0].status" \
        --raw-output 2>/dev/null || true)
      if [ "${status}" = "OK" ]; then
        echo "Backend ${backend_name} is healthy"
        return 0
      fi
    fi
    if [ "${waited}" -ge "${MAX_WAIT_SECONDS}" ]; then
      echo "Timed out waiting for backend health for ${instance_id}"
      return 1
    fi
    sleep "${SLEEP_SECONDS}"
    waited=$((waited + SLEEP_SECONDS))
  done
}

wait_for_new_backends() {
  if [ -z "${LB_ID}" ] || [ -z "${BACKEND_SET_NAME}" ]; then
    return 0
  fi

  local instance_id
  for instance_id in "$@"; do
    wait_for_backend_member "${instance_id}"
  done
}

drain_backend_for_instance() {
  local instance_id="${1:?instance id required}"
  if [ -z "${LB_ID}" ] || [ -z "${BACKEND_SET_NAME}" ]; then
    echo "LB backend drain skipped (LB_ID/BACKEND_SET_NAME not set)"
    return 0
  fi

  local backend_name
  backend_name=$(backend_name_for_instance "${instance_id}" || true)
  if [ -z "${backend_name}" ]; then
    echo "Unable to resolve backend name for ${instance_id}"
    return 1
  fi

  local weight
  weight=$(oci lb backend list \
    --load-balancer-id "${LB_ID}" \
    --backend-set-name "${BACKEND_SET_NAME}" \
    --query "data[?name=='${backend_name}'].weight | [0]" \
    --raw-output 2>/dev/null || true)
  if [ -z "${weight}" ] || [ "${weight}" = "null" ]; then
    weight=1
  fi

  echo "Draining backend ${backend_name} (weight=${weight})"
  oci lb backend update \
    --load-balancer-id "${LB_ID}" \
    --backend-set-name "${BACKEND_SET_NAME}" \
    --backend-name "${backend_name}" \
    --weight "${weight}" \
    --backup false \
    --drain true \
    --offline false

  if [ "${BACKEND_DRAIN_TIMEOUT_SECONDS}" -gt 0 ]; then
    echo "Waiting ${BACKEND_DRAIN_TIMEOUT_SECONDS}s for connections to drain"
    sleep "${BACKEND_DRAIN_TIMEOUT_SECONDS}"
  fi
}

mapfile -t old_instance_ids < <(list_instance_ids)

original_size=$(oci compute-management instance-pool get \
  --instance-pool-id "${POOL_ID}" \
  --query "data.size" \
  --raw-output)

if [ -z "${original_size}" ]; then
  echo "Failed to read instance pool size"
  exit 1
fi

target_size=$((original_size + SURGE))
if [ "${target_size}" -lt 2 ]; then
  target_size=2
fi

echo "Scaling pool to ${target_size} (original ${original_size})"
oci compute-management instance-pool update \
  --instance-pool-id "${POOL_ID}" \
  --size "${target_size}" >/dev/null

echo "Waiting for ${target_size} instances to be RUNNING"
while true; do
  running=$(oci compute-management instance-pool list-instances \
    --instance-pool-id "${POOL_ID}" \
    --compartment-id "${COMPARTMENT_ID}" \
    --query "data[?lifecycle-state=='RUNNING'] | length(@)" \
    --raw-output)
  if [ "${running}" -ge "${target_size}" ]; then
    break
  fi
  sleep "${SLEEP_SECONDS}"
done

mapfile -t current_instance_ids < <(list_instance_ids)
declare -A old_set=()
for instance_id in "${old_instance_ids[@]}"; do
  old_set["${instance_id}"]=1
done

new_instance_ids=()
for instance_id in "${current_instance_ids[@]}"; do
  if [ -z "${old_set[${instance_id}]+x}" ]; then
    new_instance_ids+=("${instance_id}")
  fi
done

if [ "${#new_instance_ids[@]}" -gt 0 ]; then
  echo "Waiting for new backend members to be healthy"
  wait_for_new_backends "${new_instance_ids[@]}"
else
  echo "No new instances detected; skipping new backend member check"
fi

wait_for_backend_set_ready "${target_size}"

for instance_id in "${old_instance_ids[@]}"; do
  drain_backend_for_instance "${instance_id}"
  echo "Terminating ${instance_id}"
  oci compute instance terminate --instance-id "${instance_id}" --force --preserve-boot-volume false
  while true; do
    running=$(oci compute-management instance-pool list-instances \
      --instance-pool-id "${POOL_ID}" \
      --compartment-id "${COMPARTMENT_ID}" \
      --query "data[?lifecycle-state=='RUNNING'] | length(@)" \
      --raw-output)
    if [ "${running}" -ge "${target_size}" ]; then
      break
    fi
    sleep "${SLEEP_SECONDS}"
  done
  wait_for_backend_set_ready "${target_size}"
done

echo "Scaling pool back to ${original_size}"
oci compute-management instance-pool update \
  --instance-pool-id "${POOL_ID}" \
  --size "${original_size}" >/dev/null
