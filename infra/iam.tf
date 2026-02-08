resource "oci_identity_dynamic_group" "instance_pool" {
  compartment_id = var.tenancy_ocid
  name           = "${var.resource_prefix}-instance-pool-dg"
  description    = "Instance pool dynamic group for vault access"
  matching_rule  = "instance.compartment.id = '${var.compartment_ocid}'"
}

resource "oci_identity_policy" "instance_pool" {
  compartment_id = var.tenancy_ocid
  name           = "${var.resource_prefix}-instance-pool-policy"
  description    = "Allow instance pool to read secrets and logs"

  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.instance_pool.name} to read secret-bundles in compartment ${var.compartment_name}",
    "Allow dynamic-group ${oci_identity_dynamic_group.instance_pool.name} to read vaults in compartment ${var.compartment_name}",
    "Allow dynamic-group ${oci_identity_dynamic_group.instance_pool.name} to read keys in compartment ${var.compartment_name}",
    "Allow dynamic-group ${oci_identity_dynamic_group.instance_pool.name} to use log-content in compartment ${var.compartment_name}"
  ]
}
