resource "oci_artifacts_container_repository" "api" {
  compartment_id = var.compartment_ocid
  display_name   = var.ocir_repo_name
  is_public      = false
}
