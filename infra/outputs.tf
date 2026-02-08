output "vcn_id" {
  value = oci_core_vcn.main.id
}

output "public_subnet_id" {
  value = oci_core_subnet.public.id
}

output "private_subnet_id" {
  value = oci_core_subnet.private.id
}

output "lb_id" {
  value = oci_load_balancer_load_balancer.main.id
}

output "lb_backend_set_name" {
  value = oci_load_balancer_backend_set.api.name
}

output "lb_public_ip" {
  value = try(oci_load_balancer_load_balancer.main.ip_address_details[0].ip_address, "")
}

output "api_nsg_id" {
  value = oci_core_network_security_group.api.id
}

output "lb_nsg_id" {
  value = oci_core_network_security_group.lb.id
}

output "instance_configuration_id" {
  value = oci_core_instance_configuration.api.id
}

output "instance_pool_id" {
  value = oci_core_instance_pool.api.id
}

output "backend_port" {
  value = var.backend_port
}

output "tenancy_ocid" {
  value = var.tenancy_ocid
}

output "compartment_name" {
  value = var.compartment_name
}

output "region" {
  value = var.region
}

output "availability_domain" {
  value = local.availability_domain
}

output "compartment_ocid" {
  value = var.compartment_ocid
}

output "instance_image_id" {
  value = var.instance_image_id
}

output "instance_shape" {
  value = var.instance_shape
}

output "instance_ocpus" {
  value = var.instance_ocpus
}

output "instance_memory_gbs" {
  value = var.instance_memory_gbs
}

output "ocir_namespace" {
  value = var.ocir_namespace
}

output "ocir_registry" {
  value = local.ocir_registry
}

output "ocir_repo_name" {
  value = var.ocir_repo_name
}

output "ocir_repo_url" {
  value = "${local.ocir_registry}/${var.ocir_namespace}/${var.ocir_repo_name}"
}

output "vault_id" {
  value = oci_kms_vault.main.id
}

output "app_env_secret_id" {
  value = oci_vault_secret.app_env.id
}

output "ocir_username_secret_id" {
  value = oci_vault_secret.ocir_username.id
}

output "ocir_auth_token_secret_id" {
  value = oci_vault_secret.ocir_auth_token.id
}

output "ocir_repo_id" {
  value = oci_artifacts_container_repository.api.id
}

output "log_group_id" {
  value = oci_logging_log_group.main.id
}

output "app_log_id" {
  value = oci_logging_log.app.id
}

output "uma_configuration_id" {
  value = oci_logging_unified_agent_configuration.app.id
}
